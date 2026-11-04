# Releasing Codex Monitor

## Plan: source first

The first release (0.2.0) is prepared as **source only**: tag the repository and publish the source. No binary is published until it can be Developer ID signed and notarized, because an app downloaded without that may be blocked by Gatekeeper and users could have to override the block by hand.

## Before tagging

1. Update `VERSION` (`MAJOR.MINOR.PATCH`) and `CHANGELOG.md`.
2. Run the automated tests:
   ```sh
   python3 -m unittest discover -s Tests -p 'test_*.py'
   ```
3. Build and smoke-test on a Mac, on the oldest macOS you can reach and on a current one if possible. The deployment target (12.0) is only what the compiler is told; record separately which macOS versions you actually ran it on:
   ```sh
   ./build.sh
   open "dist/Codex Monitor.app" --args --demo
   ```
4. Manual acceptance (these are not covered by automated tests):
   - Demo mode: window title and subtitle say "Demo data"; values are synthetic; no `codex` process starts.
   - Launch the real app from Finder (double-click), not from a terminal, while signed in to Codex: limits and credits appear.
   - With Python 3.10+ unavailable (or `CODEX_MONITOR_PYTHON` pointing at an older Python when launched from a terminal): the window shows the Python message and Refresh does not hang.
   - Kill the `python3 … feed.py` helper from Activity Monitor: the previous values stay, dimmed and marked stale, with a fixed error message. Then click Refresh: the helper restarts and values return to live.
   - Authentication failures are covered by the automated tests with isolated synthetic fixtures. Do not sign out, quit Codex, or otherwise alter your real authentication to test this (quitting the desktop app does not invalidate CLI auth anyway).
   - Change the Mac's time zone: reset times and the footer label follow it.
   - One window, no provider switch: Codex and Claude panels appear side by side (about 840 pt wide), each with its own heading, timestamp, error state and refresh. Codex balances and reset credits never appear in the Claude panel; `--demo` shows synthetic data in both panels. A failing or slow helper in one panel leaves the other unchanged; Refresh restarts only its own helper; the pin toggles the single window; quitting leaves no `feed.py` processes; the window scrolls rather than clips on a short screen.
   - Claude with no snapshot shows the setup message; after `python3 Scripts/connect_claude.py` (dry run first, then `--apply` only on a settings file you have reviewed) and a Claude Code response, the 5-hour and weekly windows appear labelled "Snapshot received". Uninstall restores the settings. Do this against a copy (`--settings`, `--install-dir`) before touching real configuration.
   - The `~/.claude.json` compatibility cache is undocumented; recheck its shape against the current Claude Code before each release, and confirm the panel degrades to "unavailable" if it changed.
   - The app bundle's `Resources` must contain `claude_usage.py`, `claude_statusline.py`, and `connect_claude.py`.
5. Check `git status` shows no credentials, `auth.json`, or real account values in the tree or in any screenshots.

## Packaging (optional, for testing or a future binary release)

```sh
bash release.sh
```

This runs `build.sh`, then writes to `dist/`:

- `Codex-Monitor-<version>-macos-<arch>.zip`, created with `ditto` so the executable bit is preserved. `<arch>` is the architecture of the Mac that built it (`arm64` or `x86_64`); the app is **not** universal. A terminal running under Rosetta reports `x86_64`.
- `Codex-Monitor-<version>-macos-<arch>.zip.sha256`, in `shasum -a 256` format. Verify with `cd dist && shasum -a 256 -c <file>.sha256`.

The script re-extracts the zip and checks the executable bit and architecture before finishing.

## Signing and notarization

Nothing here signs or notarizes. The build uses no certificates, and the scripts must not be changed to embed credentials. Do not describe a package as signed or notarized unless that was done separately with the maintainer's own Developer ID credentials. Until then:

- Do not attach the zip to a release without saying it is unsigned and not notarized.
- The app is not verified on every macOS version from 12 onward; 12.0 is only the deployment target the Swift code is compiled for, and runtime verification on the oldest OS is a separate step. The compiler may apply an ad hoc signature, which is not a Developer ID signature.
- Python 3.10+ and the Codex CLI or desktop app (for the Codex panel) are required on the user's Mac and are not bundled. The Claude panel needs only local Claude Code snapshots.

## CI

`.github/workflows/ci.yml` (push to `main`, pull requests, or manual `workflow_dispatch`) runs the tests and then `release.sh` on a hosted macOS runner. The tests and package build always run. Uploading `dist/*.zip` and `dist/*.sha256` as a workflow artifact (kept for 7 days) is opt-in: it happens only when the repository variable `UPLOAD_BUILD_ARTIFACTS` is set to `true`, and uploaded artifacts may count against the repository's artifact storage allowance. The zip is uploaded rather than the raw `.app` because artifact upload does not preserve file permissions. The artifact is unsigned and not notarized. The workflow has read-only repository permissions and does not publish releases.
