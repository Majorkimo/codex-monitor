"""Codex App Server client used by Codex Monitor.

Local extension of the MIT-licensed client vendored in
vendor/divoom_minitoo_codex/appserver.py (upstream:
https://github.com/AFrayde01/divoom-minitoo-codex). The vendored files are
left unmodified; this subclass changes only what Codex Monitor needs:

* identifies itself as Codex Monitor (not as the upstream Divoom project);
* discards buffered output and request state whenever the server restarts;
* closes the server's stdin/stdout pipes explicitly;
* restarts a server that died between polls;
* exposes a small public read-only API (read_rate_limits, read_plan).

See THIRD_PARTY_NOTICES.md for attribution.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

from divoom_minitoo_codex.appserver import (
    AccountAuthenticationError,
    AppServerError,
    CodexAppServer,
)

CLIENT_NAME = 'codex_monitor'
CLIENT_TITLE = 'Codex Monitor'


class MonitorClientError(AppServerError):
    """Failure with a fixed, safe message that the UI may show.

    Server-supplied text is never copied into user_message because it can
    contain account details.
    """
    user_message = 'Refresh failed.'


class CodexStartError(MonitorClientError):
    user_message = ('Could not start the Codex CLI. Install Codex (the CLI or the '
                    'ChatGPT/Codex desktop app), then refresh.')


class CodexAuthError(MonitorClientError):
    user_message = 'Codex is not signed in, or its session expired. Sign in to Codex, then refresh.'


def read_version():
    """Return the app version from the VERSION file, or 'unknown'."""
    here = Path(__file__).resolve().parent
    for candidate in (here / 'VERSION', here.parent / 'VERSION'):
        try:
            text = candidate.read_text(encoding='utf-8').strip()
        except OSError:
            continue
        if re.fullmatch(r'[0-9]+(\.[0-9]+){1,3}', text):
            return text
    return 'unknown'


def _close_quietly(stream):
    if stream is None:
        return
    try:
        stream.close()
    except OSError:
        pass


class MonitorClient(CodexAppServer):
    """Read-only client: only account/read and account/rateLimits/read are used."""

    def start(self):
        self.close()  # never reuse buffered bytes or ids from a previous server
        executable = shutil.which(self.executable) or self.executable
        environment = os.environ.copy()
        if self.codex_home is not None:
            environment['CODEX_HOME'] = str(self.codex_home)
        try:
            self.process = subprocess.Popen(
                [executable, 'app-server', '--stdio'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                env=environment,
            )
        except (OSError, ValueError) as exc:
            raise CodexStartError('Could not start Codex App Server.') from exc
        try:
            init = self._request('initialize', {
                'clientInfo': {
                    'name': CLIENT_NAME,
                    'title': CLIENT_TITLE,
                    'version': read_version(),
                },
            })
            if not isinstance(init, dict):
                raise AppServerError('Codex App Server returned an invalid initialize response.')
            self._notify('initialized', {})
        except Exception:
            self.close()
            raise

    def close(self):
        process = self.process
        self.process = None
        self._stdout_buffer.clear()
        self._request_id = 0
        if process is None:
            return
        _close_quietly(process.stdin)  # EOF lets a well-behaved server exit by itself
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            _close_quietly(process.stdout)

    def read_rate_limits(self):
        """Return the raw account/rateLimits/read result."""
        self._ensure_running()
        try:
            result = self._request('account/rateLimits/read', {})
        except AccountAuthenticationError:
            raise CodexAuthError('Codex rejected the saved sign-in.') from None
        if not isinstance(result, dict):
            raise MonitorClientError('Codex App Server returned an invalid usage response.')
        return result

    def read_plan(self):
        """Best-effort plan name from account/read; the account identity is discarded."""
        try:
            self._ensure_running()
            account = self.read_account()
        except Exception:
            return None
        return account.plan_type if account is not None else None

    def _ensure_running(self):
        if self.process is not None and self.process.poll() is not None:
            self.close()
        if self.process is None:
            self.start()
