# Codex & Claude Monitor for macOS

A small native macOS window that shows Codex and Claude side by side, with no switch. **Codex** shows your signed-in account's usage limits, credit balance, and limit reset credits. **Claude** shows the 5-hour and weekly limits that Claude Code has reported locally, with reset times.

Codex Monitor is an independent community project by Bits Solutions (BITS). It is not affiliated with, endorsed by, or sponsored by OpenAI or Anthropic. "Codex", "ChatGPT", "OpenAI", "Claude", and "Claude Code" belong to their owners.

Codex Monitor itself does not send prompts, read conversations, change Codex or Claude sign-in or billing settings, buy credits, or reset limits. For Claude, the official Claude Code CLI that the monitor starts for a usage refresh updates Claude Code's own local usage cache (`~/.claude.json`); that is the CLI's write, not a configuration change by the monitor. (The optional Claude status line setup below edits Claude settings, only when you run it with `--apply`.) No Divoom display or other hardware is needed.

**Status:** version 0.3.0, a source preview. There is no Developer ID signed or notarized binary; you build it yourself (see `docs/RELEASE.md`).

## Requirements

- macOS 12.0 or later (the Swift code is compiled for that deployment target).
- Xcode Command Line Tools (`swiftc`) to build.
- **Python 3.10 or later, installed separately** (python.org or Homebrew). It is not bundled. The app checks `python3` on its inherited `PATH`, then `/opt/homebrew/bin`, `/usr/local/bin`, and the python.org framework location, and uses `/usr/bin/python3` only if developer tools are installed. If `CODEX_MONITOR_PYTHON` is set it is used exclusively; if it is not executable the app reports that instead of trying another Python. If no suitable Python is found or it is too old, the window says so.
- For the Codex panel: Codex, signed in with a ChatGPT account: the `codex` CLI on `PATH`, or the CLI bundled in the installed ChatGPT or Codex desktop app (`/Applications` or `~/Applications`).
- For the Claude panel: Claude Code (a supported subscription plan) and a local usage snapshot, see "Claude panel" below. Claude does not need to be running while you use Codex Monitor.

Builds are for the architecture of the Mac that builds them (`arm64` or `x86_64`), not universal.

## Quickstart

```sh
git clone https://github.com/Majorkimo/codex-monitor.git
cd codex-monitor
```

Then build:

```sh
python3 -m unittest discover -s Tests -p 'test_*.py'   # optional
./build.sh
open "dist/Codex Monitor.app"
```

To try the interface without any Codex account:

```sh
open "dist/Codex Monitor.app" --args --demo
```

Demo mode shows synthetic values labelled "Demo data" in both panels, starts no `codex` process, and reads no account or Claude file. (Quit a running copy first; `--args` only applies to a fresh launch.)

Both providers start at launch, each with its own background helper (`feed.py --provider codex` and `--provider claude`) and its own state, timestamp, error and refresh button. A slow or failing Codex helper does not block or overwrite Claude, or the reverse; Refresh asks that panel's running helper to refresh and restarts only that panel's helper if it has died. Quitting the app stops both helpers. The single pin button affects the one window. The window opens about 820 pt wide, can be resized (minimum about 660 pt, both providers always visible), keeps the size you chose, and scrolls if the screen is short. Codex credits and reset credits appear only in the Codex panel.

The window refreshes every 60 seconds; the refresh button reads immediately and restarts the background helper only if it had stopped. The pin button keeps the window on top. If a refresh fails, the last good values stay visible with their original timestamp and a short error. For Codex, the failed snapshot is marked **Stale** immediately. For Claude, the error is shown immediately, but the values are marked **Stale** only once the original observation is older than 5 minutes. Times and number formats follow your Mac's time zone and locale; the About popover names the zone in use.

## Launching from Finder, and overrides

Apps started from Finder do not get your shell's `PATH` or environment variables. Codex Monitor adds the usual Homebrew and `~/.local/bin` locations and checks the desktop-app locations above, so a normal install works with a double-click.

For unusual setups, start the executable from a terminal so it inherits your environment:

```sh
CODEX_BIN=/path/to/codex CODEX_HOME=/path/to/codex-home CODEX_MONITOR_PYTHON=/path/to/python3 \
  "dist/Codex Monitor.app/Contents/MacOS/CodexMonitor"
```

- `CODEX_BIN`: the Codex executable to run.
- `CODEX_HOME`: Codex's state directory. If set it is passed through unchanged; if unset, Codex uses its own default.
- `CODEX_MONITOR_PYTHON`: the Python 3.10+ interpreter for the helper. When set, it is authoritative.
- `--demo` or `CODEX_MONITOR_DEMO=1`: demo mode.

Copy the app to `~/Applications` to install it.

## Data and privacy

Codex Monitor's own code makes no network connections and has no analytics or telemetry. For Claude it reads the local files described below and runs the official Claude Code CLI's `/usage` command, which contacts Anthropic as Claude Code itself does; Codex Monitor sends nothing to it. For Codex, its Python helper starts `codex app-server --stdio` and uses only two read-only requests, `account/rateLimits/read` and `account/read` (the latter, without refreshing tokens, only to learn the plan name when usage data omits it; the account email is discarded). Display data stays in local process memory, and the helper passes through your environment to Codex.

This does **not** make the tool offline. The Codex process it launches contacts OpenAI to fetch your usage and may behave according to your own Codex configuration, including any telemetry or logging settings you have. Codex Monitor does not control that. Demo mode does not start Codex.

Errors shown in the window use fixed wording; server messages, file paths, and account details are not displayed.

Credit balance and limit reset credits are different values. A missing value is shown as "Unavailable" or "N/A", never as zero. The window shows the balance with two decimal places; the exact value Codex reported is available as a tooltip on the balance, and the helper keeps every reported decimal. There is no currency conversion.

## Claude panel

The Claude panel shows usage from **local files**, and refreshing it asks the official Claude Code CLI to update one of them. The app never signs in, extracts tokens or Keychain items, reads `.credentials.json`, scans conversations or logs, calls a private API, or makes network calls itself. There is no cost estimate, and no Codex credit balance or reset credits are shown for Claude. A refresh shows "Updated <time>", not a continuous live feed. Where a value is unknown the window says "Not reported" instead of showing zero.

**How refresh works.** On launch, on a manual refresh, and on the 60 second poll (unless the cache is 60 seconds old or less), the helper runs Claude Code's built-in interactive `/usage` command: `claude --safe-mode --tools "" --permission-mode dontAsk /usage` on a pseudo terminal, with your home folder as working directory (it must already be trusted by Claude Code). `/usage` retrieves usage metadata; no model prompt is sent. Nothing is typed into the CLI, trust and sign-in prompts are never answered, terminal output is discarded unread, there is a 25 second limit, and the whole process group is stopped afterwards. Success means the cache holds account-matched, unexpired windows with a newer timestamp than before. The CLI is found via `CLAUDE_BIN` (authoritative if set), `claude` on `PATH`, then official desktop installs under `~/Library/Application Support/Claude/claude-code/` (newest first). If it is missing, too old for these options, times out (for example waiting for a trust or sign-in prompt: run Claude Code once in your home folder), or does not update the cache, the last valid usage stays visible as stale with a fixed error. `feed.py --once` only reads files unless `--refresh` is given; demo mode runs nothing.

**Limitations.** The cache is undocumented and the refresh depends on the official interactive CLI and its `--safe-mode` option; either may change without notice, in which case the panel reports it. A refresh is attempted at most about once a minute and is not a continuous live feed. If it fails, the last valid account-matched cache stays visible as stale with a fixed error, re-checked against the current time so windows that reset meanwhile are dropped. If two monitors run, only one starts the CLI (a lock file in the monitor cache folder); if that lock file is unsafe (not yours, writable by others, a symlink) or cannot be created, no CLI is started and the panel says so.

Sources and priority:

1. **Claude Code cache, once verified.** When `cachedUsageUtilization` in `~/.claude.json` (override: `CODEX_MONITOR_CLAUDE_CONFIG`, an exact file path) belongs to the signed-in account (exact match with `oauthAccount.accountUuid`) and has unexpired windows, it is used, and a newer status line snapshot never overrides it, because the snapshot carries no account id. If the cache is explicitly from another account, no numbers are shown (the snapshot is not used as a substitute); the panel asks you to sign in. A cache 60 seconds old or less is used without starting the CLI.
2. **Status line snapshot (status-line-only setups).** Claude Code's official [status line](https://code.claude.com/docs/en/statusline) passes JSON to a command; `rate_limits.five_hour` and `rate_limits.seven_day` each carry `used_percentage` (0-100) and `resets_at` (epoch seconds). Either window may be absent, and `rate_limits` appears only after the first model response in a session on supported subscription plans. `Sources/claude_statusline.py` keeps only those two windows plus a receive time, and writes them atomically with mode 0600 to `~/Library/Caches/com.bits.codexmonitor/claude-usage.json` (override: `CODEX_MONITOR_CLAUDE_SNAPSHOT`). Session ids, workspace, prompts, model and account data are never stored. It is used when there is no verified cache (for example before the first `/usage`, or when the cache format is not recognised). `feed.py --once` uses the same order (verified cache first, an explicitly mismatched account blocked, status line snapshot as fallback) but never starts the CLI unless `--refresh` is also given. As a diagnostic, `--once --refresh` currently prints two JSON lines: the cached state, then the refreshed result.

The cache entry was observed on Claude Code 2.1.309 and may change without notice; everything else in `~/.claude.json` is discarded and nothing from it is logged. It is labelled "Claude Code cache".

Times shown are when the data was observed (the cache's own timestamp), not when the app last tried. Data older than 5 minutes is marked stale but kept with its original time. Windows whose reset time has passed are dropped, never shown as 0%. A status line receipt only proves Claude Code reported it then. With no source you will see: "Open /usage in Claude Code, or connect the status line; then refresh."

### Optional: connect the status line

```sh
python3 Scripts/connect_claude.py                 # dry run: prints the plan, writes nothing
python3 Scripts/connect_claude.py --apply         # do it
python3 Scripts/connect_claude.py --uninstall --apply
```

With `--apply` it copies `claude_statusline.py` to `~/.local/share/codex-monitor` (`--install-dir`) and sets only the `statusLine` key in `~/.claude/settings.json` (`--settings`), using the absolute path of the Python that ran the installer. It backs up the original settings there (mode 0600, never overwriting an older backup) and records uninstall metadata. It refuses to continue if the settings are invalid JSON or a symlink, or if you already have a `statusLine`: it never runs or wraps another command. Uninstall removes the key only while it is still exactly what the installer wrote; otherwise it reports a conflict and changes nothing. Settings you changed in the meantime are kept, and backups are left in place. A settings file the installer created is deleted on uninstall only if it is otherwise empty. Review the dry run first; then start a new Claude Code session.

## Code layout and third-party code

- `Sources/Window.swift`: the window. `Sources/feed.py`: parsing, polling, demo data, provider selection. `Sources/monitor_client.py`: App Server client built on the vendored one. `Sources/claude_usage.py`: reads Claude snapshots. `Sources/claude_refresh.py`: the bounded `/usage` refresh. `Sources/claude_statusline.py`: the optional status line command. `Scripts/connect_claude.py`: its installer.
- `vendor/divoom_minitoo_codex/`: the small MIT-licensed App Server client from [divoom-minitoo-codex](https://github.com/AFrayde01/divoom-minitoo-codex), unmodified, with its license. Only that client module is vendored; no Divoom device code or artwork. See `THIRD_PARTY_NOTICES.md`.

## More

- `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, `docs/RELEASE.md`.
- License: MIT, see `LICENSE`.
