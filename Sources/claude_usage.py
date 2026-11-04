"""Read Claude Code usage from local snapshot files. Nothing else.

Source order, chosen deterministically:

A. The snapshot written by `claude_statusline.py`, which Claude Code runs as its
   official status line (https://code.claude.com/docs/en/statusline: `rate_limits`
   with `five_hour` / `seven_day`, each `used_percentage` and `resets_at`).
   If this file exists it is the only source; a corrupt file never falls back
   to unrelated cached numbers.
B. Only when that file is absent: the undocumented `cachedUsageUtilization`
   entry in `~/.claude.json`, accepted only when its account id exactly matches
   `oauthAccount.accountUuid`. Everything else in that file is discarded.
   `claude_refresh.py` can advance that cache through Claude Code's own
   interactive /usage command; the result is read back through these validators.

This module itself: no network, no credentials, no Keychain, no process, no log scanning.
Unknown stays unknown: expired, malformed or missing windows are dropped, never
shown as zero. Raw values and identities are never returned or logged.
"""
import json
import math
import os
import re
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

SNAPSHOT_VERSION = 1
STALE_SECONDS = 300
FUTURE_SKEW = 300  # tolerated clock difference for "received" times
MAX_TIMESTAMP = 32503680000  # year 3000
MAX_SNAPSHOT_BYTES = 4096
MAX_CONFIG_BYTES = 16 * 1024 * 1024  # ~/.claude.json can be several MB

MSG_MISSING = 'No Claude usage snapshot yet. Open /usage in Claude Code, or connect the status line; then refresh.'
MSG_EXPIRED = 'The last Claude usage snapshot has expired because its limit windows have reset. Use Claude Code, then refresh.'
MSG_UNREADABLE = 'The Claude usage snapshot is unreadable. Use Claude Code or reconnect the status line; then refresh.'
MSG_CACHE = 'The Claude Code usage cache is missing, in an unexpected format, or from another account. Connect the status line; then refresh.'
MSG_MISMATCH = 'The Claude Code usage cache is from another account than the one signed in. Sign in to Claude Code with the account you want to watch, then refresh.'
REASON_MISMATCH = 'account-mismatch'

SOURCE_STATUSLINE = 'statusline'
SOURCE_CACHE = 'cache'
LABELS = {SOURCE_STATUSLINE: 'Statusline snapshot', SOURCE_CACHE: 'Claude Code cache'}
WINDOWS = (('five_hour', '5H'), ('seven_day', '7D'))


class ClaudeUnavailable(Exception):
    """No trustworthy usage; the message is fixed wording that is safe to show."""

    def __init__(self, message, reason=None):
        Exception.__init__(self, message)
        self.user_message = message
        self.reason = reason  # REASON_MISMATCH when the cache provably belongs to another account


def snapshot_path(env=None):
    env = os.environ if env is None else env
    override = env.get('CODEX_MONITOR_CLAUDE_SNAPSHOT')
    if override:
        return Path(os.path.expanduser(override))
    return Path(os.path.expanduser('~')) / 'Library' / 'Caches' / 'com.bits.codexmonitor' / 'claude-usage.json'


def config_path(env=None):
    env = os.environ if env is None else env
    override = env.get('CODEX_MONITOR_CLAUDE_CONFIG')
    if override:
        return Path(os.path.expanduser(override))
    return Path(os.path.expanduser('~')) / '.claude.json'


# --- validation helpers: None means "cannot trust" --------------------------

def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _epoch(value):
    number = _number(value)
    if number is None or number <= 0 or number > MAX_TIMESTAMP:
        return None
    return int(number)


def _percent(value):
    number = _number(value)
    if number is None or number < 0 or number > 100:
        return None
    return int(number + 0.5)


_ISO = re.compile(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)')


def _iso_epoch(value):
    """Parse an ISO 8601 time that carries an explicit offset; None otherwise."""
    if not isinstance(value, str) or len(value) > 40:
        return None
    match = _ISO.fullmatch(value)
    if not match:
        return None
    base, _fraction, offset = match.groups()
    try:
        moment = datetime.strptime(base, '%Y-%m-%dT%H:%M:%S')
        if offset == 'Z':
            delta = timedelta(0)
        else:
            sign = 1 if offset[0] == '+' else -1
            hours, minutes = int(offset[1:3]), int(offset[4:6])
            if hours >= 24 or minutes >= 60:
                return None
            delta = sign * timedelta(hours=hours, minutes=minutes)
        return _epoch(moment.replace(tzinfo=timezone(delta)).timestamp())
    except (ValueError, OverflowError, OSError):
        return None


_MISSING = object()  # distinct from a present file whose JSON is null


def _load(path, limit, follow_links):
    """Return parsed JSON, or _MISSING if the file does not exist. Raises ValueError if unusable."""
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0)
    if not follow_links:
        flags |= getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(str(path), flags)
    except (FileNotFoundError, NotADirectoryError):
        return _MISSING
    except OSError:
        raise ValueError('open')
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit or info.st_uid != os.geteuid():
            raise ValueError('file')
        if not follow_links and info.st_mode & 0o022:
            raise ValueError('writable by others')
        with os.fdopen(fd, 'rb') as handle:
            fd = None
            data = handle.read(limit + 1)
    finally:
        if fd is not None:
            os.close(fd)
    if len(data) > limit:
        raise ValueError('size')
    try:
        return json.loads(data.decode('utf-8'))
    except (ValueError, RecursionError):
        raise ValueError('json')


def _result(source, observed, windows, now, expired_seen):
    live = [window for window in windows if window['reset'] > now]
    if not live:
        raise ClaudeUnavailable(MSG_EXPIRED if windows or expired_seen else MSG_MISSING)
    return {
        'windows': live,
        'source': source,
        'sourceLabel': LABELS[source],
        'observedAt': observed,
        'stale': now - observed > STALE_SECONDS,
    }


def _window(window_id, label, used, reset):
    if used is None or reset is None:
        return None
    return {'id': window_id, 'label': label, 'used': used, 'reset': reset}


def parse_snapshot(doc, now):
    if not isinstance(doc, dict) or type(doc.get('version')) is not int or doc['version'] != SNAPSHOT_VERSION:
        raise ClaudeUnavailable(MSG_UNREADABLE)
    received = _epoch(doc.get('received_at'))
    if received is None or received > now + FUTURE_SKEW:
        raise ClaudeUnavailable(MSG_UNREADABLE)
    windows = []
    for key, label in WINDOWS:
        item = doc.get(key)
        if isinstance(item, dict):
            window = _window(key, label, _percent(item.get('used_percentage')), _epoch(item.get('resets_at')))
            if window:
                windows.append(window)
    return _result(SOURCE_STATUSLINE, received, windows, now, False)


def parse_cache(doc, now):
    cache = doc.get('cachedUsageUtilization') if isinstance(doc, dict) else None
    account = doc.get('oauthAccount') if isinstance(doc, dict) else None
    if not isinstance(cache, dict) or not isinstance(account, dict):
        raise ClaudeUnavailable(MSG_CACHE)
    cached_id, account_id = cache.get('accountUuid'), account.get('accountUuid')
    if isinstance(cached_id, str) and cached_id and isinstance(account_id, str) and account_id and cached_id != account_id:
        raise ClaudeUnavailable(MSG_MISMATCH, REASON_MISMATCH)
    if not isinstance(cached_id, str) or not cached_id or cached_id != account_id:
        raise ClaudeUnavailable(MSG_CACHE)
    fetched = _number(cache.get('fetchedAtMs'))
    if fetched is not None and not 0 < fetched <= MAX_TIMESTAMP * 1000:
        fetched = None  # bound before converting: huge ints would overflow float
    fetched = _epoch(fetched / 1000.0) if fetched is not None else None
    if fetched is None or fetched > now + FUTURE_SKEW:
        raise ClaudeUnavailable(MSG_CACHE)
    utilization = cache.get('utilization')
    if not isinstance(utilization, dict):
        raise ClaudeUnavailable(MSG_CACHE)
    windows = []
    for key, label in WINDOWS:
        item = utilization.get(key)
        if isinstance(item, dict):
            window = _window(key, label, _percent(item.get('utilization')), _iso_epoch(item.get('resets_at')))
            if window:
                windows.append(window)
    if not windows:
        raise ClaudeUnavailable(MSG_CACHE)
    return _result(SOURCE_CACHE, fetched, windows, now, False)


def read_usage(now=None, env=None):
    """Return the usage dict or raise ClaudeUnavailable."""
    now = int(time.time() if now is None else now)
    try:
        doc = _load(snapshot_path(env), MAX_SNAPSHOT_BYTES, follow_links=False)
    except ValueError:
        raise ClaudeUnavailable(MSG_UNREADABLE)
    if doc is not _MISSING:
        _reject_mismatch(now, env)  # a snapshot never hides an explicit account mismatch
        return parse_snapshot(doc, now)
    return read_cache(now, env)


def _reject_mismatch(now, env):
    try:
        read_cache(now, env)
    except ClaudeUnavailable as exc:
        if exc.reason == REASON_MISMATCH:
            raise


def read_preferred(now=None, env=None):
    """Account-verified cache when valid, an explicit mismatch raises, else read_usage. Pure: no CLI."""
    now = int(time.time() if now is None else now)
    try:
        return read_cache(now, env)
    except ClaudeUnavailable as exc:
        if exc.reason == REASON_MISMATCH:
            raise
    return read_usage(now, env)


def read_cache(now=None, env=None):
    """Return usage from the account-matched Claude Code cache only, or raise ClaudeUnavailable."""
    now = int(time.time() if now is None else now)
    try:
        doc = _load(config_path(env), MAX_CONFIG_BYTES, follow_links=True)
    except ValueError:
        raise ClaudeUnavailable(MSG_CACHE)
    if doc is _MISSING:
        raise ClaudeUnavailable(MSG_MISSING)
    return parse_cache(doc, now)


def demo_usage(now):
    """Synthetic values for --demo; reads no file."""
    return {
        'windows': [
            {'id': 'five_hour', 'label': '5H', 'used': 41, 'reset': now + 3 * 3600},
            {'id': 'seven_day', 'label': '7D', 'used': 68, 'reset': now + 4 * 86400},
        ],
        'source': 'demo',
        'sourceLabel': 'Demo data',
        'observedAt': now,
        'stale': False,
    }
