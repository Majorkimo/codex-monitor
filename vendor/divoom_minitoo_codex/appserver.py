"""Small JSON-RPC client for Codex App Server's account rate-limit API."""

from __future__ import annotations

import json
import os
import re
import select
import shutil
import subprocess
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


class AppServerError(RuntimeError):
    """Raised when Codex App Server cannot return account rate limits."""


class AccountAuthenticationError(AppServerError):
    """A profile's account request was rejected with HTTP 401."""

    def __init__(self, message: str, *, codex_home: Path | None = None) -> None:
        super().__init__(message)
        self.codex_home = codex_home


@dataclass(frozen=True)
class UsageWindow:
    label: str
    used_percent: int
    window_duration_mins: int | None
    resets_at: int | None


@dataclass(frozen=True)
class ResetCredit:
    expires_at: int | None
    expiration_known: bool = True


@dataclass(frozen=True)
class ResetCredits:
    available_count: int = 0
    credits: tuple[ResetCredit, ...] | None = None


@dataclass(frozen=True)
class UsageSnapshot:
    plan_type: str | None
    windows: tuple[UsageWindow, ...]
    reset_credits: ResetCredits = ResetCredits()
    account_email: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class CodexAccount:
    auth_type: str
    email: str | None = field(default=None, repr=False)
    plan_type: str | None = None


def _window_label(minutes: int | None, fallback: str) -> str:
    if minutes == 300:
        return "5H"
    if minutes == 10_080:
        return "7D"
    if minutes is None or minutes <= 0:
        return fallback.upper()
    if minutes % 1_440 == 0:
        return f"{minutes // 1_440}D"
    if minutes % 60 == 0:
        return f"{minutes // 60}H"
    return f"{minutes}M"


def parse_usage_result(result: dict[str, Any]) -> UsageSnapshot:
    """Normalize an App Server rateLimits/read result for the renderer."""
    buckets = result.get("rateLimitsByLimitId") or {}
    codex_bucket = buckets.get("codex") if isinstance(buckets, dict) else None
    bucket = codex_bucket or result.get("rateLimits") or {}
    if not isinstance(bucket, dict):
        raise AppServerError("Codex returned an unexpected rate-limit response.")

    windows: list[UsageWindow] = []
    for name in ("primary", "secondary"):
        item = bucket.get(name)
        if not isinstance(item, dict) or item.get("usedPercent") is None:
            continue
        try:
            used = max(0, min(100, round(float(item["usedPercent"]))))
        except (TypeError, ValueError):
            continue
        raw_duration = item.get("windowDurationMins")
        try:
            duration = int(raw_duration) if raw_duration is not None else None
        except (TypeError, ValueError):
            duration = None
        raw_reset = item.get("resetsAt")
        try:
            reset = int(raw_reset) if raw_reset is not None else None
        except (TypeError, ValueError):
            reset = None
        windows.append(
            UsageWindow(
                label=_window_label(duration, name),
                used_percent=used,
                window_duration_mins=duration,
                resets_at=reset,
            )
        )

    if not windows:
        raise AppServerError(
            "Codex App Server did not return usage windows. Confirm you are signed in "
            "with a ChatGPT account that includes Codex."
        )

    reset_summary = result.get("rateLimitResetCredits")
    if isinstance(reset_summary, dict):
        try:
            available_count = max(0, int(reset_summary.get("availableCount", 0)))
        except (TypeError, ValueError):
            available_count = 0

        raw_credits = reset_summary.get("credits")
        credits: tuple[ResetCredit, ...] | None = None
        if isinstance(raw_credits, list):
            parsed_credits: list[ResetCredit] = []
            for item in raw_credits:
                if not isinstance(item, dict) or item.get("status") != "available":
                    continue
                raw_expiry = item.get("expiresAt")
                try:
                    expires_at = int(raw_expiry) if raw_expiry is not None else None
                except (TypeError, ValueError):
                    expires_at = None
                parsed_credits.append(
                    ResetCredit(
                        expires_at=expires_at,
                        expiration_known="expiresAt" in item,
                    )
                )
            credits = tuple(parsed_credits)
        reset_credits = ResetCredits(available_count, credits)
    else:
        reset_credits = ResetCredits()

    return UsageSnapshot(
        plan_type=(bucket.get("planType") or result.get("planType")),
        windows=tuple(windows),
        reset_credits=reset_credits,
    )


class CodexAppServer:
    """Launch Codex App Server and ask it for the signed-in account's limits."""

    def __init__(
        self,
        executable: str | None = None,
        timeout: float = 20.0,
        codex_home: Path | None = None,
    ) -> None:
        self.executable = executable or os.environ.get("CODEX_BIN") or "codex"
        self.timeout = timeout
        self.codex_home = codex_home
        self.process: subprocess.Popen[bytes] | None = None
        self._stdout_buffer = bytearray()
        self._request_id = 0

    def start(self) -> None:
        executable = shutil.which(self.executable) or self.executable
        environment = os.environ.copy()
        if self.codex_home is not None:
            environment["CODEX_HOME"] = str(self.codex_home)
        try:
            self.process = subprocess.Popen(
                [executable, "app-server", "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                env=environment,
            )
        except OSError as exc:
            raise AppServerError(
                f"Could not start Codex App Server using {self.executable!r}. "
                "Install the Codex CLI or set CODEX_BIN to its path."
            ) from exc

        try:
            init = self._request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "divoom_minitoo_codex",
                        "title": "Divoom Codex Usage",
                        "version": "0.2.0",
                    }
                },
            )
            if not isinstance(init, dict):
                raise AppServerError("Codex App Server returned an invalid initialize response.")
            self._notify("initialized", {})
        except Exception:
            self.close()
            raise

    def read_account(self) -> CodexAccount | None:
        """Read account identity without requesting a token refresh."""
        if self.process is None:
            self.start()
        result = self._request("account/read", {"refreshToken": False})
        if not isinstance(result, dict) or "account" not in result:
            raise AppServerError("Codex App Server returned an invalid account response.")
        account = result.get("account")
        if account is None:
            return None
        if not isinstance(account, dict) or not isinstance(account.get("type"), str):
            raise AppServerError("Codex App Server returned an invalid account response.")

        def label(value: object) -> str | None:
            if not isinstance(value, str):
                return None
            return "".join(character for character in value if character.isprintable()).strip() or None

        return CodexAccount(
            auth_type=account["type"],
            email=label(account.get("email")),
            plan_type=label(account.get("planType")),
        )

    def read_usage(self) -> UsageSnapshot:
        if self.process is None:
            self.start()
        result = self._request("account/rateLimits/read", {})
        if not isinstance(result, dict):
            raise AppServerError("Codex App Server returned an invalid usage response.")
        snapshot = parse_usage_result(result)
        # Query the same App Server/profile as the quotas. Do not read auth
        # files or retain a previous account's email if this lookup fails.
        try:
            account = self.read_account()
        except AppServerError:
            return snapshot  # Account identity is optional; keep usage available.
        if account is None or account.auth_type != "chatgpt":
            return snapshot
        return replace(
            snapshot, account_email=account.email,
            plan_type=snapshot.plan_type or account.plan_type,
        )

    def close(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"method": method, "params": params})

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        self._request_id += 1
        request_id = self._request_id
        self._write({"method": method, "id": request_id, "params": params})
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            message = self._read_message(deadline)
            if message is None or message.get("id") != request_id:
                continue
            error = message.get("error")
            if error:
                description = error.get("message", "unknown JSON-RPC error")
                if (
                    method.startswith("account/")
                    and re.search(r"(?:^|\D)401(?:\D|$)", str(description))
                    and "unauthorized" in str(description).casefold()
                ):
                    raise AccountAuthenticationError(
                        f"Codex App Server: {description}", codex_home=self.codex_home,
                    )
                raise AppServerError(f"Codex App Server: {description}")
            if "result" not in message:
                raise AppServerError("Codex App Server sent a response without a result.")
            return message["result"]
        raise AppServerError(f"Timed out waiting for Codex App Server method {method}.")

    def _write(self, message: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise AppServerError(
                "Codex App Server is not running. Run this from a normal macOS session "
                "where Codex can access its local state."
            )
        try:
            process.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
            process.stdin.flush()
        except OSError as exc:
            raise AppServerError("Could not send a request to Codex App Server.") from exc

    def _read_message(self, deadline: float) -> dict[str, Any] | None:
        while True:
            newline = self._stdout_buffer.find(b"\n")
            if newline >= 0:
                raw = bytes(self._stdout_buffer[:newline])
                del self._stdout_buffer[: newline + 1]
                try:
                    decoded = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                return decoded if isinstance(decoded, dict) else None

            process = self.process
            if process is None or process.stdout is None:
                raise AppServerError("Codex App Server stdout closed unexpectedly.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([process.stdout], [], [], remaining)
            if not ready:
                return None
            chunk = os.read(process.stdout.fileno(), 65_536)
            if not chunk:
                code = process.poll()
                raise AppServerError(
                    "Codex App Server exited before replying"
                    + (f" (code {code})." if code is not None else ".")
                )
            self._stdout_buffer.extend(chunk)

    def __enter__(self) -> "CodexAppServer":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
