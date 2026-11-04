"""Claude Code /usage refresh: fake CLI on a real pty, hermetic directories, no real Claude.

The fake CLI (fake_claude.py) only writes the temporary config named by the
test environment. Nothing here starts the real `claude` binary or touches the
network or any real account.
"""
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import ROOT  # noqa: E402
from test_claude import ACCOUNT, NOW, PRIVATE, TempCase, claude_time, config_doc, snapshot_doc  # noqa: E402

import claude_refresh  # noqa: E402
import claude_usage  # noqa: E402
from claude_refresh import ClaudeRefresher  # noqa: E402
from claude_usage import ClaudeUnavailable  # noqa: E402
from feed import ClaudeMonitor  # noqa: E402

FAKE = Path(__file__).resolve().parent / 'fake_claude.py'
NINE_HOURS = 9 * 3600


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RefreshCase(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        self.home = self.dir / 'home'
        self.home.mkdir()
        self.log = self.dir / 'fake.log'

    def make_cli(self, directory, mode):
        directory.mkdir(parents=True, exist_ok=True)
        cli = directory / 'claude'
        cli.write_text('#!/bin/sh\nexec "%s" -I "%s" %s "%s" "$@"\n' % (sys.executable, FAKE, mode, self.log))
        cli.chmod(0o755)
        return cli

    def refresher(self, mode, timeout=20, **extra):
        cli = self.make_cli(self.dir / 'bin', mode)
        env = dict(self.env, CLAUDE_BIN=str(cli), FAKE_FETCHED=str(NOW), FAKE_RESET=claude_time(NOW + 3600))
        env.update(extra)
        return ClaudeRefresher(env=env, home=self.home, clock=lambda: NOW, timeout=timeout)

    def old_cache(self, age=NINE_HOURS, **kwargs):
        kwargs.setdefault('five', (12.5, claude_time(NOW + 3600)))
        kwargs.setdefault('seven', (40, claude_time(NOW + 86400)))
        self.put(self.config, config_doc(fetched_ms=(NOW - age) * 1000, **kwargs))

    def events(self, name):
        if not self.log.exists():
            return []
        return [e for e in map(json.loads, self.log.read_text().splitlines()) if e['event'] == name]

    def assert_gone(self, pids):
        deadline = time.time() + 5
        while time.time() < deadline and any(alive(pid) for pid in pids):
            time.sleep(0.05)
        self.assertEqual([pid for pid in pids if alive(pid)], [])

    def all_pids(self):
        return [e['pid'] for e in self.events('start') + self.events('child')]


class RefreshSuccess(RefreshCase):
    def test_cache_timestamp_advances_with_fixed_safe_invocation(self):
        self.old_cache()
        self.put(self.snapshot, snapshot_doc(received=NOW - NINE_HOURS))  # stale statusline must not mask the refresh
        data = self.refresher('update')(NOW)
        self.assertEqual((data['source'], data['observedAt'], data['stale'], data['refreshed']), ('cache', NOW, False, True))
        self.assertEqual([w['used'] for w in data['windows']], [21, 44])
        self.assertNotIn('refreshError', data)
        start = self.events('start')[0]
        self.assertEqual(start['args'], ['--safe-mode', '--tools', '', '--permission-mode', 'dontAsk', '/usage'])
        self.assertEqual(os.path.realpath(start['cwd']), os.path.realpath(self.home))
        self.assertTrue(start['tty'])
        self.assertEqual((start['pid'], start['pgid'], start['sid']), (start['pid'],) * 3)  # own session and group
        self.assertEqual(self.events('stdin'), [])  # nothing was ever sent to the CLI
        self.assert_gone(self.all_pids())

    def test_corrupt_statusline_does_not_block_a_refreshed_cache(self):
        self.old_cache()
        self.put(self.snapshot, 'garbage')
        data = self.refresher('update')(NOW)
        self.assertEqual((data['source'], data['observedAt']), ('cache', NOW))

    def test_fresh_cache_does_not_start_the_cli(self):
        self.old_cache(age=30)
        refresher = self.refresher('update')
        refresher.runner = lambda *a: self.fail('CLI must not start for a cache that is 60 s old or less')
        data = refresher(NOW)
        self.assertEqual((data['source'], data['observedAt']), ('cache', NOW - 30))
        self.assertNotIn('refreshed', data)

    def test_cache_older_than_a_minute_starts_the_cli(self):
        self.old_cache(age=61)
        self.assertTrue(self.refresher('update')(NOW)['refreshed'])
        self.assertEqual(len(self.events('start')), 1)

    def test_busy_lock_means_no_second_cli(self):
        import fcntl
        self.old_cache()
        refresher = self.refresher('update')
        refresher.runner = lambda *a: self.fail('duplicate refresh')
        with open(self.dir / 'claude-refresh.lock', 'w') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            data = refresher(NOW)
        self.assertTrue(data['stale'])
        self.assertNotIn('refreshError', data)


class RefreshFailure(RefreshCase):
    def test_timeout_keeps_valid_stale_usage_and_kills_every_process(self):
        self.old_cache()
        data = self.refresher('hang', timeout=1.5)(NOW)
        self.assertEqual(data['refreshError'], claude_refresh.MSG_TIMEOUT)
        self.assertTrue(data['stale'])
        self.assertEqual((data['observedAt'], [w['used'] for w in data['windows']]), (NOW - NINE_HOURS, [13, 40]))
        self.assertEqual(len(self.events('child')), 1)
        self.assert_gone(self.all_pids())

    def test_timeout_without_data_is_a_fixed_actionable_error(self):
        with self.assertRaises(ClaudeUnavailable) as caught:
            self.refresher('hang', timeout=1)(NOW)
        self.assertIn(claude_refresh.MSG_TIMEOUT, caught.exception.user_message)
        self.assert_gone(self.all_pids())

    def test_exit_without_cache_update_is_reported_not_faked(self):
        self.old_cache()
        data = self.refresher('noupdate')(NOW)
        self.assertEqual((data['refreshError'], data['observedAt']), (claude_refresh.MSG_NO_UPDATE, NOW - NINE_HOURS))

    def test_nonzero_exit_means_unsupported_cli(self):
        self.old_cache()
        self.assertEqual(self.refresher('fail')(NOW)['refreshError'], claude_refresh.MSG_UNSUPPORTED)

    def test_other_account_cache_is_never_shown(self):
        self.old_cache()
        with self.assertRaises(ClaudeUnavailable) as caught:
            self.refresher('update', timeout=1.5, FAKE_ACCOUNT='someone-else')(NOW)
        self.assertIn(claude_usage.MSG_MISMATCH, caught.exception.user_message)
        self.assertIn(claude_refresh.MSG_TIMEOUT, caught.exception.user_message)
        self.assertNotIn('someone-else', caught.exception.user_message)

    def test_expired_windows_are_never_shown_as_usage(self):
        self.old_cache()
        with self.assertRaises(ClaudeUnavailable) as caught:
            self.refresher('update', timeout=1.5, FAKE_RESET=claude_time(NOW - 5))(NOW)
        self.assertIn('expired', caught.exception.user_message)

    def test_expired_old_cache_is_not_masked_by_failed_refresh(self):
        self.old_cache(five=(90, claude_time(NOW - 5)), seven=None)
        with self.assertRaises(ClaudeUnavailable) as caught:
            self.refresher('noupdate')(NOW)
        self.assertIn('expired', caught.exception.user_message)

    def test_valid_cache_is_kept_over_a_corrupt_or_newer_statusline_after_failed_refresh(self):
        self.old_cache()
        for snapshot in ('garbage', snapshot_doc(received=NOW - 10)):
            self.put(self.snapshot, snapshot)
            data = self.refresher('noupdate')(NOW)
            self.assertEqual((data['source'], data['observedAt'], data['refreshError']),
                             ('cache', NOW - NINE_HOURS, claude_refresh.MSG_NO_UPDATE))

    def test_newer_statusline_never_masks_a_valid_cache_even_when_fresh(self):
        self.old_cache(age=30)
        self.put(self.snapshot, snapshot_doc(received=NOW - 5))
        data = self.refresher('update')(NOW)
        self.assertEqual((data['source'], data['observedAt']), ('cache', NOW - 30))

    def test_statusline_never_resurrects_another_accounts_numbers(self):
        self.old_cache()
        self.put(self.snapshot, snapshot_doc(received=NOW - 10))
        with self.assertRaises(ClaudeUnavailable) as caught:
            self.refresher('update', timeout=1.5, FAKE_ACCOUNT='someone-else')(NOW)
        message = caught.exception.user_message
        self.assertIn(claude_usage.MSG_MISMATCH, message)
        self.assertNotIn('someone-else', message)

    def test_windows_that_reset_during_the_refresh_are_not_returned(self):
        self.old_cache()
        clock = [NOW]
        refresher = self.refresher('update')
        refresher.clock = lambda: clock[0]

        def runner(*_args):
            clock[0] = NOW + 7200  # the 5H window (reset NOW + 3600) ended while waiting
            return claude_refresh.TIMED_OUT
        refresher.runner = runner
        data = refresher(NOW)
        self.assertEqual([w['id'] for w in data['windows']], ['seven_day'])
        self.assertEqual(data['refreshError'], claude_refresh.MSG_TIMEOUT)

    def test_unacquirable_lock_fails_closed_without_starting_the_cli(self):
        self.old_cache()
        lock = self.dir / 'claude-refresh.lock'
        for prepare in (lambda: lock.write_text('') or lock.chmod(0o666),
                        lambda: (lock.unlink(), lock.symlink_to(self.dir / 'elsewhere'))):
            prepare()
            refresher = self.refresher('update')
            refresher.runner = lambda *a: self.fail('CLI must not start without a safe lock')
            data = refresher(NOW)
            self.assertEqual(data['refreshError'], claude_refresh.MSG_LOCK)
            self.assertTrue(data['stale'])

    def test_failed_refresh_keeps_statusline_fallback(self):
        self.put(self.snapshot, snapshot_doc(received=NOW - 100))
        data = self.refresher('noupdate')(NOW)
        self.assertEqual((data['source'], data['refreshError']), ('statusline', claude_refresh.MSG_NO_UPDATE))

    def test_start_failure(self):
        refresher = self.refresher('update')
        refresher.runner = lambda *a: claude_refresh.START_FAILED
        with self.assertRaises(ClaudeUnavailable) as caught:
            refresher(NOW)
        self.assertIn(claude_refresh.MSG_START, caught.exception.user_message)

    def test_sigterm_and_system_exit_clean_up_the_process_group(self):
        script = (
            'import sys, signal, time\n'
            'sys.path[:0] = [%r]\n'
            'import claude_refresh\n'
            'signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))\n'
            'claude_refresh.ClaudeRefresher(home=sys.argv[1])(int(time.time()))\n' % str(ROOT / 'Sources'))
        cli = self.make_cli(self.dir / 'bin', 'hang')
        env = dict(os.environ, **self.env, CLAUDE_BIN=str(cli))
        runner = subprocess.Popen([sys.executable, '-I', '-c', script, str(self.home)], env=env)
        self.addCleanup(lambda: runner.poll() is None and runner.kill())
        deadline = time.time() + 15
        while time.time() < deadline and not self.events('child'):
            time.sleep(0.05)
        pids = self.all_pids()
        self.assertEqual(len(pids), 2)
        runner.terminate()
        runner.wait(timeout=15)
        self.assert_gone(pids)


class CliResolution(RefreshCase):
    def desktop(self, version, mode):
        directory = self.home / 'Library' / 'Application Support' / 'Claude' / 'claude-code' / version / 'abc' / 'claude.app' / 'Contents' / 'MacOS'
        return self.make_cli(directory, mode)

    def test_explicit_override_is_authoritative(self):
        self.make_cli(self.dir / 'path', 'update')
        env = {'CLAUDE_BIN': str(self.dir / 'missing'), 'PATH': str(self.dir / 'path')}
        self.assertEqual(claude_refresh.cli_candidates(env, self.home), [])
        env['CLAUDE_BIN'] = str(self.dir / 'path' / 'claude')
        self.assertEqual(claude_refresh.cli_candidates(env, self.home), [env['CLAUDE_BIN']])

    def test_path_then_desktop_newest_first_without_duplicates(self):
        older, newer = self.desktop('2.1.9', 'update'), self.desktop('2.1.10', 'update')
        path_cli = self.make_cli(self.dir / 'path', 'update')
        found = claude_refresh.cli_candidates({'PATH': str(self.dir / 'path')}, self.home)
        self.assertEqual(found, [str(path_cli), str(newer), str(older)])
        self.assertEqual(claude_refresh.cli_candidates({'PATH': ''}, self.home), [str(newer), str(older)])

    def test_no_cli_is_actionable_and_keeps_stale_data(self):
        self.old_cache()
        refresher = ClaudeRefresher(env=dict(self.env, PATH=''), home=self.home, clock=lambda: NOW)
        data = refresher(NOW)
        self.assertEqual((data['refreshError'], data['stale']), (claude_refresh.MSG_NO_CLI, True))

    def test_unsupported_path_cli_falls_through_to_desktop_cli(self):
        self.old_cache()
        self.make_cli(self.dir / 'path', 'fail')
        self.desktop('2.1.309', 'update')
        env = dict(self.env, PATH=str(self.dir / 'path'), FAKE_FETCHED=str(NOW), FAKE_RESET=claude_time(NOW + 3600))
        data = ClaudeRefresher(env=env, home=self.home, clock=lambda: NOW, timeout=20)(NOW)
        self.assertEqual((data['source'], data['observedAt']), ('cache', NOW))
        self.assertEqual(len(self.events('start')), 2)
        self.assert_gone(self.all_pids())


class MonitorIntegration(RefreshCase):
    def test_refresh_reader_is_optional_and_state_is_honest(self):
        self.old_cache()
        plain = ClaudeMonitor(lambda now: __import__('claude_usage').read_usage(now, self.env), clock=lambda: NOW)
        self.assertTrue(plain.poll()['stale'])
        monitor = ClaudeMonitor(lambda now: self.fail('reader'), clock=lambda: NOW, refresh_reader=self.refresher('noupdate'))
        state = monitor.poll()
        self.assertEqual((state['available'], state['stale'], state['error']), (True, True, claude_refresh.MSG_NO_UPDATE))
        self.assertNotIn(PRIVATE + ACCOUNT, json.dumps(state))
        updated = ClaudeMonitor(lambda now: self.fail('reader'), clock=lambda: NOW, refresh_reader=self.refresher('update')).poll()
        self.assertEqual((updated['refreshed'], updated['stale'], updated['observedAt']), (True, False, NOW))
        self.assertNotIn('error', updated)
        self.assertFalse(updated['live'])

    def test_production_early_poll_rejects_explicit_mismatch_despite_snapshot(self):
        self.old_cache(cached='someone-else')
        self.put(self.snapshot, snapshot_doc(received=NOW - 5))
        early = ClaudeMonitor(lambda now: claude_usage.read_preferred(now, self.env), clock=lambda: NOW,
                              refresh_reader=self.refresher('update')).poll(refresh=False)
        self.assertEqual((early['available'], early['windows']), (False, []))
        self.assertIn(claude_usage.MSG_MISMATCH, early['notice'])
        self.assertNotIn('someone-else', json.dumps(early))
        with self.assertRaises(ClaudeUnavailable):
            claude_usage.read_usage(NOW, self.env)

    def test_read_preferred_keeps_cache_priority_and_statusline_only_setups(self):
        self.put(self.snapshot, snapshot_doc(received=NOW - 5))
        self.assertEqual(claude_usage.read_preferred(NOW, self.env)['source'], 'statusline')  # no config at all
        self.old_cache(age=30)
        self.assertEqual(claude_usage.read_preferred(NOW, self.env)['source'], 'cache')
        self.assertEqual(claude_usage.read_usage(NOW, self.env)['source'], 'statusline')

    def test_poll_without_refresh_uses_the_plain_reader(self):
        seen = []
        def plain(now):
            seen.append('plain')
            return claude_usage.demo_usage(now)

        def refresh(now):
            seen.append('refresh')
            raise ClaudeUnavailable('Fixed.')
        monitor = ClaudeMonitor(plain, clock=lambda: NOW, refresh_reader=refresh)
        state = monitor.poll(refresh=False)
        self.assertEqual((seen, state['available']), (['plain'], True))
        failed = monitor.poll()
        self.assertEqual((seen, failed['available'], failed['notice']), (['plain', 'refresh'], False, 'Fixed.'))


if __name__ == '__main__':
    unittest.main()
