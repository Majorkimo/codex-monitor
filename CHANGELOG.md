# Changelog

## 0.3.0 (unreleased source preview)

- **Claude refresh fix.** Refresh used to re-read cached files only, so Claude data could be hours old while the window looked current. The Claude helper now advances Claude Code's own usage cache by running the official CLI's built-in interactive `/usage` command (metadata only, no model prompt) on a pseudo terminal with fixed arguments `--safe-mode --tools "" --permission-mode dontAsk /usage`, in your home folder. It runs at launch, on manual refresh, and on the 60 second poll, but is skipped while the cache is 60 seconds old or less. At most 25 seconds; the whole process group is stopped on success, failure, timeout, SIGTERM and exit. Terminal output is discarded unread and never stored, nothing is typed into the CLI, and trust or sign-in prompts are never answered (they show as a timeout with guidance).
- Success is decided only by the cache itself: account-matched, unexpired windows and a newer timestamp than before. Otherwise the previous valid usage stays visible, marked stale, with a fixed error (no CLI, unsupported CLI, timeout, no update). Another account's data and reset windows are never shown. A fresh cache wins over an older or unreadable status line snapshot; the status line remains the fallback when the CLI is unavailable.
- CLI lookup: `CLAUDE_BIN` (authoritative when set), `claude` on `PATH`, then the official desktop installs under `~/Library/Application Support/Claude/claude-code/`, newest first; a CLI that rejects the safe arguments is skipped in favour of the next. `feed.py --once` still reads files only unless `--refresh` is added. Demo mode runs nothing.
- **Redesigned window.** Native macOS look: adaptive light and dark system colors, SF Pro and SF Symbols, two material cards side by side (Codex blue, Claude orange accents), aligned 5-hour and weekly sections, "Not reported" for missing windows, credit balance and reset credits as distinct Codex-only details, per-provider status and refresh buttons, a shared pin control, a resizable window (minimum about 660 pt, default about 820 pt) that keeps your size across refreshes, and readable stale/error text instead of dimmed panels.
- New tests with a fake CLI on a real pty (no real Claude Code is started).
- `claude_refresh.py` is bundled by `build.sh`.

## 0.2.0 - 2026-11-04 (source preview)

First public source preview.

- Launching from Finder: the helper no longer overwrites `CODEX_HOME`, finds Python in common install locations, and shows an actionable message if Python is older than 3.10 or missing.
- Refresh restarts the helper if it has stopped, and a watchdog restarts one that hangs.
- Times use the Mac's current time zone and locale. The fixed Africa/Cairo zone and the `zoneinfo` dependency are gone.
- Hardened parsing of usage and credit data (booleans, NaN/infinity, negative or absurd numbers). Missing credits show as unavailable, never zero; credit balances keep every decimal digit; windows with the same label no longer collide; a response without usage windows still shows credits.
- If a refresh fails, the last successful values stay on screen, visibly marked stale. Error text is fixed wording and never includes server or exception details.
- `--demo` mode (also `CODEX_MONITOR_DEMO=1`) shows clearly labelled synthetic data without starting Codex.
- App Server client: identifies as Codex Monitor instead of the upstream Divoom project, drops buffered output when the server restarts, closes pipes explicitly, and restarts a server that died between polls (in `Sources/monitor_client.py`; vendored files unchanged).
- Tests now include a fake local app-server subprocess.
- Build: macOS 12.0 deployment target, host-architecture build, version and license notices inside the app bundle, no deletion of unrelated directories. `release.sh` creates a zip with a SHA-256 file.
- Claude support: Codex and Claude are shown side by side in one window (about 840 pt wide, no provider switch), each panel with its own helper (`feed.py --provider codex|claude`), state, timestamp, error and refresh; one pin control; both helpers stop on quit. Claude shows 5-hour and weekly limits with reset times from local snapshots only (no network, credentials, or log scanning): the official status line (`Sources/claude_statusline.py`) first, or, if no snapshot file exists, the undocumented `~/.claude.json` usage cache when its account id matches. Stale, expired, malformed, and unknown values are never shown as zero. Credit balance and reset credits are hidden for Claude.
- Optional `Scripts/connect_claude.py`: dry-run-by-default, reversible installer that sets only the `statusLine` key.
- New tests for both Claude sources, the status line writer, the installer, and provider-selected feeds.
- Documentation: README rewritten, plus SECURITY, CONTRIBUTING, and third-party notices.

## 0.1.0

- Native English macOS dashboard for Codex usage, credit balance, and limit reset credits.
- Read-only access through the local Codex App Server.
- No external hardware, hooks, or hosted service.
