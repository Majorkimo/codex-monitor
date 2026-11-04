"""Read-only Codex quota and credit feed for the native desktop monitor.

Prints one JSON object per line on stdout. A line on stdin (or the 60 second
timer) triggers a refresh; closing stdin stops the feed. `--once` prints a
single snapshot, `--demo` serves synthetic data without starting Codex.

Keep this module importable by old interpreters so that the Python version
check below can explain the problem instead of crashing with a SyntaxError.
"""
import json
import math
import os
import re
import select
import shutil
import signal
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

MIN_PYTHON = (3, 10)
REFRESH_SECONDS = 60
MAX_TIMESTAMP = 32503680000  # year 3000; later values are not plausible reset times
MAX_COUNT = 10 ** 9
MAX_BALANCE_TEXT = 128  # longest balance text parsed at all
MAX_DECIMAL_MAGNITUDE = 30 # decimal exponent bound for displayed credit balances

MSG_NO_DATA = 'Codex returned no usage data. Confirm Codex is signed in with a ChatGPT account.'
MSG_GENERIC = 'Refresh failed.'
MSG_SETUP = 'Codex Monitor files are incomplete. Rebuild or reinstall the app.'


class FeedError(Exception):
    """Failure whose message is safe to show in the UI."""

    def __init__(self, message):
        Exception.__init__(self, message)
        self.user_message = message


def python_version_error(version_info=None):
    """Return an actionable message if the interpreter is too old, else None."""
    version = tuple((version_info or sys.version_info)[:3])
    if version >= MIN_PYTHON:
        return None
    return ('Python %d.%d or newer is required, but this app found Python %s. '
            'Install a current Python 3 (python.org or Homebrew), then reopen Codex Monitor.'
            % (MIN_PYTHON[0], MIN_PYTHON[1], '.'.join(str(part) for part in version)))


def resolve_codex():
    """Honor an explicit override, then PATH, then installed desktop bundles."""
    override = os.environ.get('CODEX_BIN')
    if override:
        return override
    executable = shutil.which('codex')
    if executable:
        return executable
    for root in (Path('/Applications'), Path.home() / 'Applications'):
        for relative in (
            'ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex',
            'Codex.app/Contents/Resources/codex',
        ):
            candidate = root / relative
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return 'codex'


# --- response parsing -------------------------------------------------------
# Every helper returns None for anything it cannot trust. Unknown must never
# be displayed as zero.

def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _timestamp(value):
    number = _number(value)
    if number is None or number <= 0 or number > MAX_TIMESTAMP:
        return None
    return int(number)


def _count(value):
    number = _number(value)
    if number is None or number < 0 or number > MAX_COUNT or number != int(number):
        return None
    return int(number)


def _percent(value):
    number = _number(value)
    if number is None or number < 0:
        return None
    if number >= 100:
        return 100  # exhausted; also avoids float overflow on absurd integers
    return int(number + 0.5)


def _text(value, limit=32):
    if not isinstance(value, str):
        return None
    cleaned = ''.join(character for character in value if character.isprintable()).strip()
    return cleaned[:limit] or None


_DECIMAL = re.compile(r'[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?')


def parse_balance(value):
    """Return a plain decimal string, preserving every digit, or None."""
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, float):
            if not math.isfinite(value):
                return None
            text = repr(value)
        elif isinstance(value, int):
            text = str(value)
        elif isinstance(value, str):
            text = value.strip()
        else:
            return None
        if len(text) > MAX_BALANCE_TEXT or not _DECIMAL.fullmatch(text):
            return None
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    # Bound the exponent (zero included) so formatting can never allocate a huge string.
    if abs(number.as_tuple().exponent) > MAX_DECIMAL_MAGNITUDE:
        return None
    if number.is_zero():
        number = number.copy_abs()  # "-0" is just zero
    elif abs(number.adjusted()) > MAX_DECIMAL_MAGNITUDE:
        return None
    return format(number, 'f')


def _window_label(minutes, fallback):
    if minutes == 300:
        return '5H'
    if minutes == 10080:
        return '7D'
    if not minutes:
        return fallback.upper()
    if minutes % 1440 == 0:
        return '%dD' % (minutes // 1440)
    if minutes % 60 == 0:
        return '%dH' % (minutes // 60)
    return '%dM' % minutes


def _windows(bucket):
    windows = []
    for name in ('primary', 'secondary'):
        item = bucket.get(name)
        if not isinstance(item, dict):
            continue
        used = _percent(item.get('usedPercent'))
        if used is None:
            continue
        minutes = _count(item.get('windowDurationMins'))
        windows.append({
            'id': name,  # unique even when both windows share a label
            'label': _window_label(minutes, name),
            'used': used,
            'reset': _timestamp(item.get('resetsAt')),
        })
    return windows


def normalize(result, plan=None):
    """Turn an account/rateLimits/read result into the UI snapshot.

    Missing windows do not hide valid credits. Raises FeedError if the result
    holds nothing displayable at all.
    """
    if not isinstance(result, dict):
        raise FeedError(MSG_NO_DATA)
    buckets = result.get('rateLimitsByLimitId')
    bucket = buckets.get('codex') if isinstance(buckets, dict) else None
    if not isinstance(bucket, dict) or not bucket:
        bucket = result.get('rateLimits')
    if not isinstance(bucket, dict):
        bucket = {}

    credits = bucket.get('credits')
    if not isinstance(credits, dict):
        credits = {}
    balance = parse_balance(credits.get('balance'))
    unlimited = credits.get('unlimited') is True

    # Limit reset credits are a separate account feature from the credit balance.
    resets = result.get('rateLimitResetCredits')
    reset_count = None
    expiries = []
    if isinstance(resets, dict):
        reset_count = _count(resets.get('availableCount'))
        entries = resets.get('credits')
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get('status') == 'available':
                expiry = _timestamp(entry.get('expiresAt'))
                if expiry is not None:
                    expiries.append(expiry)

    snapshot = {
        'plan': _text(bucket.get('planType')) or _text(result.get('planType')) or _text(plan),
        'windows': _windows(bucket),
        'credits': {'balance': balance, 'unlimited': unlimited},
        'resetCredits': reset_count,
        'resetCreditExpiry': min(expiries) if expiries else None,
    }
    if not (snapshot['windows'] or balance is not None or unlimited or reset_count is not None):
        raise FeedError(MSG_NO_DATA)
    return snapshot


# --- polling ----------------------------------------------------------------

def describe_failure(exc):
    """Map any failure to a fixed message; never expose exception text."""
    message = getattr(exc, 'user_message', None)
    return message if isinstance(message, str) else MSG_GENERIC


class DemoClient(object):
    """Synthetic data source for --demo. Starts no process and reads no account."""

    def __init__(self, clock=time.time):
        self.clock = clock

    def read_rate_limits(self):
        now = int(self.clock())
        return {
            'rateLimitsByLimitId': {'codex': {
                'planType': 'demo',
                'primary': {'usedPercent': 37, 'windowDurationMins': 300, 'resetsAt': now + 2 * 3600},
                'secondary': {'usedPercent': 62.4, 'windowDurationMins': 10080, 'resetsAt': now + 3 * 86400},
                'credits': {'hasCredits': True, 'unlimited': False, 'balance': '1234.56789'},
            }},
            'rateLimitResetCredits': {
                'availableCount': 2,
                'credits': [
                    {'status': 'available', 'expiresAt': now + 20 * 86400},
                    {'status': 'available', 'expiresAt': now + 9 * 86400},
                ],
            },
        }

    def read_plan(self):
        return None

    def close(self):
        pass


class Monitor(object):
    """Polls a client and keeps the last successful snapshot for stale display."""

    def __init__(self, client, demo=False, clock=time.time):
        self.client = client
        self.demo = demo
        self.clock = clock
        self.snapshot = None

    def poll(self):
        try:
            data = normalize(self.client.read_rate_limits())
            if data['plan'] is None:
                data['plan'] = _text(self.client.read_plan())
        except Exception as exc:
            self.client.close()  # next poll starts a fresh server
            state = dict(self.snapshot) if self.snapshot else {}
            state.update(
                live=False,
                stale=self.snapshot is not None,
                error='%s Retrying in %d seconds.' % (describe_failure(exc), REFRESH_SECONDS),
            )
        else:
            data.update(live=True, stale=False, updatedAt=int(self.clock()))
            self.snapshot = data
            state = dict(data)
        if self.demo:
            state['demo'] = True
        return state


class ClaudeMonitor(object):
    """Reads local Claude Code usage snapshots only; keeps no state between polls.

    Nothing from an earlier poll or from Codex is reused: when the snapshot is
    unusable the state says so and carries no windows.
    """

    def __init__(self, reader, demo=False, clock=time.time):
        self.reader = reader
        self.demo = demo
        self.clock = clock

    def poll(self):
        now = int(self.clock())
        try:
            data = self.reader(now)
        except Exception as exc:
            message = getattr(exc, 'user_message', None)
            state = {'provider': 'claude', 'live': False, 'stale': False, 'available': False,
                     'windows': [], 'notice': message if isinstance(message, str) else MSG_GENERIC,
                     'updatedAt': now}
        else:
            state = {'provider': 'claude', 'live': False, 'stale': data['stale'], 'available': True,
                     'windows': data['windows'], 'source': data['source'],
                     'sourceLabel': data['sourceLabel'], 'observedAt': data['observedAt'],
                     'updatedAt': now}
        if self.demo:
            state['demo'] = True
        return state


def wait_for_request(timeout, fd=0):
    """Block for a refresh request: 'refresh', 'timeout', or 'eof' (stdin closed)."""
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return 'timeout'
    # One read drains every queued request, so repeated clicks coalesce.
    return 'refresh' if os.read(fd, 4096) else 'eof'


def emit(state):
    print(json.dumps(state, allow_nan=False), flush=True)


def make_client():
    from monitor_client import MonitorClient
    override = os.environ.get('CODEX_HOME')
    home = Path(os.path.expanduser(override)) if override else None
    return MonitorClient(executable=resolve_codex(), codex_home=home)


def parse_provider(args):
    """Return 'codex' (default), 'claude', or None for an unknown value."""
    for index, arg in enumerate(args):
        if arg == '--provider':
            value = args[index + 1] if index + 1 < len(args) else ''
        elif arg.startswith('--provider='):
            value = arg.split('=', 1)[1]
        else:
            continue
        return value if value in ('codex', 'claude') else None
    return 'codex'


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    demo = '--demo' in args
    provider = parse_provider(args)
    if provider is None:
        emit({'live': False, 'fatal': True, 'error': MSG_SETUP})
        return 1
    try:
        if provider == 'claude':
            import claude_usage
            client = None
            monitor = ClaudeMonitor(claude_usage.demo_usage if demo else claude_usage.read_usage, demo=demo)
        else:
            client = DemoClient() if demo else make_client()
            monitor = Monitor(client, demo=demo)
    except ImportError:
        emit({'live': False, 'fatal': True, 'error': MSG_SETUP, 'provider': provider})
        return 1

    def terminate(_signum, _frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    try:
        while True:
            state = monitor.poll()
            state['provider'] = provider
            emit(state)
            if '--once' in args or wait_for_request(REFRESH_SECONDS) == 'eof':
                break
    except BrokenPipeError:
        pass  # the app went away
    finally:
        if client is not None:
            client.close()
    return 0


if __name__ == '__main__':
    problem = python_version_error()
    if problem:
        emit({'live': False, 'fatal': True, 'error': problem})
        sys.exit(1)
    sys.exit(main())
