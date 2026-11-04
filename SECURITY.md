# Security policy

## Scope

Codex Monitor is a local, read-only viewer. It starts `codex app-server --stdio` on your Mac and requests usage data over local stdio. Its own code has no network access and does not read or change Codex authentication files, hooks, or configuration. The Codex process it launches does contact OpenAI and reads its own state, as it would on its own.

The Claude panel only reads local files: the snapshot written by the optional status line command (`~/Library/Caches/com.bits.codexmonitor/claude-usage.json`, mode 0600, holding only two usage percentages, their reset times, and a receive time) and, when that is absent, one usage-cache entry of `~/.claude.json` after an exact account-id match. It never reads Claude credentials or Keychain items, makes network requests, or runs Claude. The optional installer `Scripts/connect_claude.py` edits only the `statusLine` key of Claude Code settings, and only with `--apply`; it keeps a private backup and refuses to replace an existing `statusLine`. Review its dry run before applying it.

Security reports of interest include anything that could expose account data or credentials, run unintended commands, modify Codex state, or make the app act on something other than read-only usage data.

Out of scope: behavior of Codex itself, the ChatGPT/Codex apps, or OpenAI services. Report those to OpenAI.

## Supported versions

Only the latest release and the `main` branch receive fixes.

## Reporting a vulnerability

- If this repository has GitHub private vulnerability reporting enabled, use the **Report a vulnerability** button on the repository's Security tab.
- If that option is not shown, open a public issue that only asks for a private way to report a security problem. Do **not** put vulnerability details in it.

Do not include credentials, tokens, `auth.json` contents, email addresses, or other real account data in any report, issue, log, or screenshot. Synthetic reproduction steps are enough.
