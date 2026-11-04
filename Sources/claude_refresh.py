"""Advance Claude Code's own usage cache by running its built-in interactive /usage command.

`/usage` only retrieves account usage metadata; no model prompt is sent. The
official CLI is started on a pseudo terminal with fixed arguments, in the user's
home folder, with no standard input from this program. Every terminal byte is
drained and discarded: nothing is logged, shown, parsed or saved. Success is
decided only by the cache file itself, read back through the `claude_usage`
validators (account match, unexpired windows, a timestamp newer than before).

Not used: `-p/--print`, OAuth tokens, Keychain, private HTTP APIs, shells,
bypass flags. Trust and sign-in prompts are never answered; they surface as a
timeout with actionable guidance. The whole process group is always cleaned up.
"""
import fcntl
import glob
import os
import pty
import re
import select
import shutil
import signal
import struct
import stat
import subprocess
import termios
import time
from pathlib import Path

import claude_usage
from claude_usage import ClaudeUnavailable

CLI_ARGS = ('--safe-mode', '--tools', '', '--permission-mode', 'dontAsk', '/usage')
TIMEOUT_SECONDS = 25
FRESH_SECONDS = 60  # a cache this young is used as is; the CLI is not started
GRACE_SECONDS = 2
DESKTOP_GLOB = 'Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude'

MSG_NO_CLI = 'Claude Code was not found. Install it or set CLAUDE_BIN, then refresh. Showing the last known usage.'
MSG_UNSUPPORTED = 'This Claude Code version cannot run a safe usage refresh. Update Claude Code, then refresh.'
MSG_TIMEOUT = ('Claude Code did not finish the usage refresh in time. Open Claude Code once in your home folder '
               'to confirm trust or sign in, then refresh.')
MSG_NO_UPDATE = 'Claude Code finished but did not update its usage cache for the signed-in account. Sign in to Claude Code, then refresh.'
MSG_START = 'Claude Code could not be started.'
MSG_LOCK = ('The usage refresh lock file is missing, not owned by you, or writable by others, so Claude Code was not started. '
            'Delete claude-refresh.lock in the monitor cache folder, then refresh.')

OK, TIMED_OUT, START_FAILED, FAILED, EXITED_CLEAN = 'ok', 'timeout', 'start', 'failed', 'exited'


def _executable(path):
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _version_key(path):
    parts = Path(path).parts
    index = parts.index('claude-code') + 1 if 'claude-code' in parts else 0
    return [tuple(int(number) for number in re.findall(r'\d+', part)) for part in parts[index:index + 2]]


def cli_candidates(env=None, home=None):
    """Executables to try, in order. An explicit CLAUDE_BIN is authoritative."""
    env = os.environ if env is None else env
    home = os.path.expanduser('~') if home is None else str(home)
    override = env.get('CLAUDE_BIN')
    if override:
        override = os.path.expanduser(override)
        return [override] if _executable(override) else []
    found = []
    on_path = shutil.which('claude', path=env.get('PATH'))
    if on_path:
        found.append(on_path)
    desktop = sorted(glob.glob(os.path.join(home, DESKTOP_GLOB)), key=_version_key, reverse=True)
    result = []
    for path in found + desktop:
        if _executable(path) and os.path.realpath(path) not in [os.path.realpath(p) for p in result]:
            result.append(path)
    return result


def _terminate(proc):
    """Stop the whole process group: SIGTERM, a short grace period, then SIGKILL. Reaps the child."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        deadline = time.monotonic() + GRACE_SECONDS
        while time.monotonic() < deadline:
            if proc.poll() is not None and not _group_alive(proc.pid):
                return
            time.sleep(0.02)
    proc.wait()


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run_cli(cli, home, ready, deadline, env):
    """Run `cli` with the fixed arguments on a pty until ready() or deadline (monotonic)."""
    master, slave = pty.openpty()
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))
    except OSError:
        pass
    try:
        proc = subprocess.Popen([cli, *CLI_ARGS], stdin=slave, stdout=slave, stderr=slave, cwd=home,
                                env=env, close_fds=True, start_new_session=True)
    except (OSError, ValueError):
        os.close(master)
        os.close(slave)
        return START_FAILED
    os.close(slave)
    eof = False
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return TIMED_OUT
            if eof:
                time.sleep(min(0.05, remaining))
            elif select.select([master], [], [], min(0.2, remaining))[0]:
                try:
                    eof = not os.read(master, 65536)  # drained and discarded, never inspected
                except OSError:
                    eof = True
            if ready():
                return OK
            code = proc.poll()
            if code is not None:
                if ready():
                    return OK
                return EXITED_CLEAN if code == 0 else FAILED
    finally:
        try:
            _terminate(proc)
        finally:
            os.close(master)


class _Watch(object):
    """True once the cache holds account-matched, unexpired usage newer than the baseline."""

    def __init__(self, env, clock, baseline, started):
        self.env, self.clock, self.baseline, self.started = env, clock, baseline, started
        self.key = None
        self.result = False

    def __call__(self):
        try:
            info = os.stat(str(claude_usage.config_path(self.env)))
        except OSError:
            return False
        key = (info.st_mtime_ns, info.st_size)
        if key == self.key:
            return self.result
        try:
            data = claude_usage.read_cache(int(self.clock()), self.env)
        except ClaudeUnavailable:
            return False  # mid-write, another account or expired: look again on the next change
        self.key = key
        self.result = data['observedAt'] > self.baseline and data['observedAt'] >= self.started - 2
        return self.result


class _Lock(object):
    """Cross-process guard so two monitors never start Claude Code at once."""

    def __init__(self, env):
        self.path = claude_usage.snapshot_path(env).with_name('claude-refresh.lock')
        self.fd = None
        self.busy = False
        self.error = None  # fixed message when the lock cannot be trusted: the CLI must not start

    def __enter__(self):
        fd = None
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise OSError('unsafe lock file')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, PermissionError):
                os.close(fd)
                self.busy = True  # another monitor is refreshing right now
                return self
            self.fd = fd
        except OSError:
            if fd is not None:
                os.close(fd)
            self.error = MSG_LOCK
        return self

    def __exit__(self, *_exc):
        if self.fd is not None:
            os.close(self.fd)  # closing releases the lock
            self.fd = None


class ClaudeRefresher(object):
    """Reader for ClaudeMonitor: returns usage like claude_usage.read_usage, refreshing first when needed."""

    def __init__(self, env=None, home=None, runner=run_cli, clock=time.time, timeout=TIMEOUT_SECONDS):
        self.env = os.environ if env is None else env
        self.home = os.path.expanduser('~') if home is None else str(home)
        self.runner, self.clock, self.timeout = runner, clock, timeout

    def __call__(self, now):
        try:
            cached = claude_usage.read_cache(now, self.env)
        except ClaudeUnavailable:
            cached = None
        if cached and 0 <= now - cached['observedAt'] <= FRESH_SECONDS:
            return cached
        with _Lock(self.env) as lock:
            if lock.error:
                error, refreshed = lock.error, False
            elif lock.busy:
                error, refreshed = None, False
            else:
                error = self._refresh(cached['observedAt'] if cached else 0)
                refreshed = error is None
        # The refresh may have taken 25 s: validate again at the current time, not at `now`.
        later = int(self.clock())
        try:
            # A verified account-matched cache always wins; a newer unbound status line never overrides it.
            data = claude_usage.read_preferred(later, self.env)
        except ClaudeUnavailable as exc:
            raise ClaudeUnavailable(' '.join(part for part in (error, exc.user_message) if part))
        if data['source'] != claude_usage.SOURCE_CACHE:
            refreshed = False  # status line only setups, or no cache yet
        if refreshed:
            data['refreshed'] = True
        elif error:
            data['refreshError'] = error
        return data

    def _refresh(self, baseline):
        candidates = cli_candidates(self.env, self.home)
        if not candidates:
            return MSG_NO_CLI
        deadline = time.monotonic() + self.timeout
        env = dict(os.environ)
        env.update(self.env)
        env.setdefault('TERM', 'xterm-256color')
        error = MSG_START
        for cli in candidates:
            watch = _Watch(self.env, self.clock, baseline, int(self.clock()))
            outcome = self.runner(cli, self.home, watch, deadline, env)
            if outcome == OK:
                return None
            if outcome == TIMED_OUT:
                return MSG_TIMEOUT
            if outcome == EXITED_CLEAN:
                return MSG_NO_UPDATE
            error = MSG_UNSUPPORTED if outcome == FAILED else MSG_START
        return error
