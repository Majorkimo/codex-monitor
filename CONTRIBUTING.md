# Contributing

Thanks for helping. This is a deliberately small project; please keep changes focused.

## Build and test

Requirements: macOS 12 or later, Xcode Command Line Tools (`xcode-select --install`), Python 3.10 or later.

```sh
python3 -m unittest discover -s Tests -p 'test_*.py'   # no Codex install, sign-in, or network needed
./build.sh                                             # builds dist/Codex Monitor.app for this Mac
open "dist/Codex Monitor.app" --args --demo            # manual UI check with synthetic data
```

## Ground rules

- Read-only: no prompts, no changes to Codex hooks, configuration, or authentication, no purchases, no automatic limit resets, no API keys.
- Claude support reads local snapshots only: no OAuth/Keychain/`.credentials.json`, no API or network calls, no running Claude, no conversation or log scanning, no cost estimates. Never persist or print anything from the status line input except the two usage windows. `Sources/claude_statusline.py` must stay a single self-contained stdlib file; `Scripts/connect_claude.py` may change only the `statusLine` key and must stay dry-run by default. Tests must use temp directories and synthetic data, never a real `~/.claude*` file.
- Provider state is never shared: each panel has its own `MonitorState` and `FeedRunner`; do not show one provider's values in the other's panel.
- Unknown values stay unknown. Never show a missing credit balance or count as zero.
- Test fixtures must be synthetic (for example a balance of `1234.56789`). Never commit real balances, emails, tokens, or screenshots of a real account.
- Tests should drive production code, including the fake app-server in `Tests/fake_app_server.py`, rather than restate expected literals.
- `Sources/feed.py` must stay importable by Python older than 3.10 so its version check can explain the problem; use no new dependencies.
- Swift code must compile for the macOS 12.0 deployment target set in `build.sh`; avoid newer SwiftUI APIs.
- Files under `vendor/` are third-party and unmodified (see `THIRD_PARTY_NOTICES.md`). Put adjustments in `Sources/monitor_client.py`.

## Pull requests

Describe what changed and how you checked it (tests run, demo mode, Finder launch if relevant). Contributions are accepted under the project's MIT license.

Security issues: see `SECURITY.md` instead of opening a public issue with details.
