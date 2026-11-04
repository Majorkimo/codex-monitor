"""Claude Code status line command for Codex Monitor (optional, opt-in).

Claude Code runs this with a JSON document on stdin
(https://code.claude.com/docs/en/statusline). It keeps only
`rate_limits.five_hour` and `rate_limits.seven_day` (`used_percentage`,
`resets_at`), stamps the receive time, and writes them atomically (mode 0600)
to ~/Library/Caches/com.bits.codexmonitor/claude-usage.json, or to the path in
CODEX_MONITOR_CLAUDE_SNAPSHOT. The rest of stdin (session id, workspace,
prompts, model, account data) is never stored or printed.

Missing or malformed windows replace the file with an empty snapshot, so an
earlier account's numbers can never look fresh. It prints one short line made
of validated numbers only, and every failure is silent. No network, no timers.

Self-contained on purpose: the installer copies this single file.
"""
import json
import math
import os
import sys
import tempfile
import time

VERSION = 1
MAX_INPUT = 1024 * 1024
MAX_TIMESTAMP = 32503680000
WINDOWS = (('five_hour', '5h'), ('seven_day', '7d'))


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def build_snapshot(raw, now):
    """Return the snapshot dict to persist. Never raises; bad input gives no windows."""
    snapshot = {'version': VERSION, 'received_at': int(now)}
    try:
        document = json.loads(raw)
    except Exception:
        return snapshot
    limits = document.get('rate_limits') if isinstance(document, dict) else None
    if not isinstance(limits, dict):
        return snapshot
    for key, _label in WINDOWS:
        try:  # each window stands alone: a bad one never hides a good one
            window = _window(limits.get(key), now)
        except Exception:
            window = None
        if window:
            snapshot[key] = window
    return snapshot


def _window(item, now):
    if not isinstance(item, dict):
        return None
    used = _number(item.get('used_percentage'))
    reset = _number(item.get('resets_at'))
    if used is None or reset is None or not 0 <= used <= 100:
        return None
    if not 0 < reset <= MAX_TIMESTAMP or int(reset) <= now:
        return None  # already reset: not a current value
    return {'used_percentage': used, 'resets_at': int(reset)}


def render(snapshot):
    parts = ['%s %d%%' % (label, int(snapshot[key]['used_percentage'] + 0.5))
             for key, label in WINDOWS if key in snapshot]
    return 'Claude ' + ' | '.join(parts) if parts else 'Claude usage n/a'


def snapshot_path(env):
    override = env.get('CODEX_MONITOR_CLAUDE_SNAPSHOT')
    if override:
        return os.path.expanduser(override)
    return os.path.join(os.path.expanduser('~'), 'Library', 'Caches', 'com.bits.codexmonitor', 'claude-usage.json')


def write_snapshot(path, snapshot):
    """Atomic replace; the temporary file is private from creation."""
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.claude-usage.', dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as handle:
            fd = None
            json.dump(snapshot, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def run(raw, env, now):
    snapshot = build_snapshot(raw, now)
    try:
        write_snapshot(snapshot_path(env), snapshot)
    except Exception:
        pass  # never disturb Claude Code
    return render(snapshot)


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raw = b''
        line = run(raw, os.environ, int(time.time()))
        sys.stdout.write(line + '\n')
        sys.stdout.flush()
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
