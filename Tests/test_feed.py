"""Feed behavior: response parsing, stale handling, demo mode, and the real script.

Fixtures are synthetic. Nothing here needs a Codex install, sign-in, or network.
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import FakeCodex, ROOT  # noqa: E402  (also sets up import paths)

import feed  # noqa: E402
from feed import DemoClient, FeedError, Monitor, normalize, parse_balance, resolve_codex  # noqa: E402
from monitor_client import CodexAuthError, MonitorClient  # noqa: E402

FEED_SCRIPT = ROOT / 'Sources' / 'feed.py'
SECRET = 'sk-SYNTHETIC-SECRET'
GOOD_WINDOW = {'usedPercent': 25, 'windowDurationMins': 300, 'resetsAt': 1791100000}


def make_result(primary=GOOD_WINDOW, secondary=None, credits=None, reset=None, plan=None):
    bucket = {}
    if primary is not None:
        bucket['primary'] = primary
    if secondary is not None:
        bucket['secondary'] = secondary
    if credits is not None:
        bucket['credits'] = credits
    if plan is not None:
        bucket['planType'] = plan
    value = {'rateLimitsByLimitId': {'codex': bucket}}
    if reset is not None:
        value['rateLimitResetCredits'] = reset
    return value


class ExecutableResolution(unittest.TestCase):
    @patch('feed.shutil.which', return_value='/custom/bin/codex')
    def test_path_is_preferred(self, _which):
        with patch.dict('feed.os.environ', {}, clear=True):
            self.assertEqual(resolve_codex(), '/custom/bin/codex')

    def test_explicit_override_is_preserved(self):
        with patch.dict('feed.os.environ', {'CODEX_BIN': '/chosen/codex'}, clear=True):
            self.assertEqual(resolve_codex(), '/chosen/codex')

    @patch('feed.os.access', return_value=True)
    @patch('feed.Path.is_file', return_value=True)
    @patch('feed.shutil.which', return_value=None)
    def test_desktop_launch_finds_bundled_cli(self, *_mocks):
        with patch.dict('feed.os.environ', {}, clear=True):
            self.assertEqual(resolve_codex(), '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')

    @patch('feed.Path.is_file', return_value=False)
    @patch('feed.shutil.which', return_value=None)
    def test_missing_cli_keeps_existing_start_error(self, *_mocks):
        with patch.dict('feed.os.environ', {}, clear=True):
            self.assertEqual(resolve_codex(), 'codex')


class PythonVersion(unittest.TestCase):
    def test_old_interpreters_get_an_actionable_message(self):
        message = feed.python_version_error((3, 9, 6))
        self.assertIn('3.10', message)
        self.assertIn('3.9.6', message)
        self.assertIn('Install', message)

    def test_supported_interpreters_pass(self):
        self.assertIsNone(feed.python_version_error((3, 10, 0)))
        self.assertIsNone(feed.python_version_error((3, 13, 1, 'final', 0)))
        self.assertIsNone(feed.python_version_error())  # the interpreter running the tests


class CreditStates(unittest.TestCase):
    def test_balance_and_reset_count_remain_separate(self):
        data = normalize(make_result(
            credits={'balance': '1234.56789', 'unlimited': False},
            reset={'availableCount': 3, 'credits': [
                {'status': 'available', 'expiresAt': 1791200000},
                {'status': 'available', 'expiresAt': 1791300000},
                {'status': 'used', 'expiresAt': 1},
            ]}))
        self.assertEqual(data['credits']['balance'], '1234.56789')
        self.assertEqual(data['resetCredits'], 3)
        self.assertEqual(data['resetCreditExpiry'], 1791200000)
        self.assertEqual(data['windows'][0]['used'], 25)

    def test_reset_credits_do_not_stand_in_for_missing_balance(self):
        data = normalize(make_result(reset={'availableCount': 2}))
        self.assertIsNone(data['credits']['balance'])
        self.assertEqual(data['resetCredits'], 2)

    def test_missing_data_is_unknown_not_zero(self):
        data = normalize(make_result())
        self.assertIsNone(data['credits']['balance'])
        self.assertIsNone(data['resetCredits'])
        self.assertIsNone(data['resetCreditExpiry'])

    def test_zero_and_unlimited_are_explicit(self):
        self.assertEqual(normalize(make_result(credits={'balance': '0'}))['credits']['balance'], '0')
        self.assertTrue(normalize(make_result(credits={'unlimited': True}))['credits']['unlimited'])
        self.assertEqual(normalize(make_result(reset={'availableCount': 0}))['resetCredits'], 0)

    def test_unlimited_requires_a_real_true(self):
        for value in ('true', 1, 'yes', None):
            self.assertFalse(normalize(make_result(credits={'unlimited': value, 'balance': '1'}))['credits']['unlimited'])

    def test_balance_precision_is_preserved(self):
        cases = {
            '1234.56789': '1234.56789',
            ' 12.50 ': '12.50',
            '0.00': '0.00',
            '1e3': '1000',
            '-0': '0',
            1234.56789: '1234.56789',
            42: '42',
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(parse_balance(given), expected)

    def test_invalid_balances_are_not_displayed(self):
        invalid = ['NaN', 'Infinity', '-Infinity', '-1', 'invalid', '', ' ', '1_0', '0x10', '1e999999',
                   '1e-999999', True, False, None, [], {}, float('nan'), float('inf'), -0.5, -3]
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(parse_balance(value))
                self.assertIsNone(normalize(make_result(credits={'balance': value}))['credits']['balance'])

    def test_huge_integer_balance_does_not_raise(self):
        self.assertIsNone(parse_balance(10 ** 5000))

    def test_pathological_balances_are_bounded(self):
        for value in ('0e-999999999', '0E+999999999', '0.' + '0' * 200, '1.' + '0' * 200, '0e-31', '1e-31', '9' * 129):
            with self.subTest(value=value[:20]):
                self.assertIsNone(parse_balance(value))
        self.assertEqual(parse_balance('0e-30'), '0.' + '0' * 30)
        self.assertEqual(parse_balance('1.' + '0' * 20), '1.' + '0' * 20)


class Windows(unittest.TestCase):
    def used(self, value):
        data = normalize(make_result(primary={'usedPercent': value}, credits={'balance': '1'}))
        return [window['used'] for window in data['windows']]

    def test_used_percent_is_clamped_and_rounded(self):
        self.assertEqual(self.used(24.6), [25])
        self.assertEqual(self.used(0), [0])
        self.assertEqual(self.used(100.4), [100])
        self.assertEqual(self.used(250), [100])
        self.assertEqual(self.used(10 ** 400), [100])

    def test_invalid_used_percent_drops_the_window(self):
        for value in (True, False, None, '25', float('nan'), float('inf'), -1, -0.1, [], {}):
            with self.subTest(value=value):
                self.assertEqual(self.used(value), [])

    def test_invalid_reset_times_are_unknown(self):
        for value in (True, None, '1791100000', float('nan'), float('inf'), -1, 0, 1e20):
            with self.subTest(value=value):
                window = normalize(make_result(
                    primary={'usedPercent': 5, 'resetsAt': value}))['windows'][0]
                self.assertIsNone(window['reset'])

    def test_labels_follow_duration(self):
        labels = {300: '5H', 10080: '7D', 1440: '1D', 120: '2H', 45: '45M'}
        for minutes, label in labels.items():
            with self.subTest(minutes=minutes):
                window = normalize(make_result(primary={'usedPercent': 1, 'windowDurationMins': minutes}))['windows'][0]
                self.assertEqual(window['label'], label)
        for minutes in (None, 0, -5, True, float('nan'), 'x'):
            with self.subTest(minutes=minutes):
                window = normalize(make_result(primary={'usedPercent': 1, 'windowDurationMins': minutes}))['windows'][0]
                self.assertEqual(window['label'], 'PRIMARY')

    def test_windows_with_the_same_label_keep_distinct_ids(self):
        data = normalize(make_result(
            primary={'usedPercent': 10, 'windowDurationMins': 300},
            secondary={'usedPercent': 20, 'windowDurationMins': 300}))
        self.assertEqual([window['label'] for window in data['windows']], ['5H', '5H'])
        ids = [window['id'] for window in data['windows']]
        self.assertEqual(len(set(ids)), 2)

    def test_no_windows_does_not_hide_valid_credits(self):
        data = normalize(make_result(
            primary=None, credits={'balance': '1234.56789'}, reset={'availableCount': 1}))
        self.assertEqual(data['windows'], [])
        self.assertEqual(data['credits']['balance'], '1234.56789')
        self.assertEqual(data['resetCredits'], 1)

    def test_malformed_containers_are_ignored(self):
        for result in (
            {'rateLimitsByLimitId': 'x', 'rateLimits': [], 'rateLimitResetCredits': 7},
            {'rateLimitsByLimitId': {'codex': 5}, 'rateLimits': 'x'},
            {'rateLimits': {'primary': 'x', 'secondary': 5, 'credits': 'x'}},
        ):
            with self.subTest(result=result):
                with self.assertRaises(FeedError):
                    normalize(result)

    def test_nothing_displayable_is_an_error(self):
        for result in ({}, None, [], 'text', make_result(primary=None)):
            with self.subTest(result=result):
                with self.assertRaises(FeedError):
                    normalize(result)

    def test_legacy_rate_limits_key_is_still_read(self):
        data = normalize({'rateLimits': {'primary': GOOD_WINDOW, 'planType': 'pro'}})
        self.assertEqual(data['windows'][0]['used'], 25)
        self.assertEqual(data['plan'], 'pro')

    def test_plan_is_sanitized_and_falls_back(self):
        self.assertEqual(normalize(make_result(plan='  pr\x00o\n '))['plan'], 'pro')
        self.assertEqual(normalize(make_result(plan=5), plan='plus')['plan'], 'plus')
        self.assertEqual(len(normalize(make_result(plan='x' * 500))['plan']), 32)
        self.assertIsNone(normalize(make_result())['plan'])


class ResetCredits(unittest.TestCase):
    def test_invalid_counts_are_unknown_not_zero(self):
        for value in (True, False, -1, 2.5, float('nan'), float('inf'), '3', None, [], 10 ** 12):
            with self.subTest(value=value):
                data = normalize(make_result(credits={'balance': '1'}, reset={'availableCount': value}))
                self.assertIsNone(data['resetCredits'])

    def test_whole_float_counts_are_accepted(self):
        self.assertEqual(normalize(make_result(reset={'availableCount': 3.0}))['resetCredits'], 3)

    def test_expiry_ignores_unusable_entries(self):
        entries = [
            {'status': 'available', 'expiresAt': True},
            {'status': 'available', 'expiresAt': float('nan')},
            {'status': 'available', 'expiresAt': -5},
            {'status': 'available', 'expiresAt': '1791200000'},
            {'status': 'available'},
            {'status': 'expired', 'expiresAt': 1791100001},
            'not a dict',
            {'status': 'available', 'expiresAt': 1791300000},
            {'status': 'available', 'expiresAt': 1791250000},
        ]
        data = normalize(make_result(reset={'availableCount': 2, 'credits': entries}))
        self.assertEqual(data['resetCreditExpiry'], 1791250000)


class ScriptedClient(object):
    """In-memory client that replays results and exceptions."""

    def __init__(self, *steps, plan=None):
        self.steps = list(steps)
        self.plan = plan
        self.closed = 0

    def read_rate_limits(self):
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    def read_plan(self):
        return self.plan

    def close(self):
        self.closed += 1


class Clock(object):
    def __init__(self, now=1_800_000_000):
        self.now = now

    def __call__(self):
        return self.now


class Polling(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def good(self):
        return make_result(secondary={'usedPercent': 60, 'windowDurationMins': 10080},
                           credits={'balance': '1234.56789'}, reset={'availableCount': 2})

    def test_success_is_live_and_timestamped(self):
        state = Monitor(ScriptedClient(self.good()), clock=self.clock).poll()
        self.assertTrue(state['live'])
        self.assertFalse(state['stale'])
        self.assertEqual(state['updatedAt'], self.clock.now)
        self.assertNotIn('error', state)
        self.assertNotIn('demo', state)
        json.dumps(state, allow_nan=False)

    def test_failure_keeps_last_success_marked_stale(self):
        client = ScriptedClient(self.good(), RuntimeError('token=' + SECRET))
        monitor = Monitor(client, clock=self.clock)
        first = monitor.poll()
        self.clock.now += 600
        second = monitor.poll()
        self.assertFalse(second['live'])
        self.assertTrue(second['stale'])
        self.assertEqual(second['updatedAt'], first['updatedAt'])  # time of the last success
        self.assertEqual(second['windows'], first['windows'])
        self.assertEqual(second['credits'], first['credits'])
        self.assertEqual(second['resetCredits'], first['resetCredits'])
        self.assertTrue(second['error'])
        self.assertEqual(client.closed, 1)  # forces a fresh server on the next poll

    def test_failure_without_prior_success_is_not_stale(self):
        state = Monitor(ScriptedClient(RuntimeError('boom')), clock=self.clock).poll()
        self.assertFalse(state['live'])
        self.assertFalse(state['stale'])
        self.assertNotIn('windows', state)
        self.assertNotIn('updatedAt', state)

    def test_errors_never_leak_exception_text(self):
        for exc in (RuntimeError('token=' + SECRET), OSError('/Users/someone/.codex/auth.json'),
                    KeyError(SECRET), ValueError('person@example.invalid')):
            with self.subTest(exc=exc):
                state = Monitor(ScriptedClient(exc), clock=self.clock).poll()
                encoded = json.dumps(state)
                for fragment in (SECRET, 'auth.json', 'example.invalid', 'someone'):
                    self.assertNotIn(fragment, encoded)

    def test_known_failures_use_their_fixed_message(self):
        state = Monitor(ScriptedClient(CodexAuthError('server text ' + SECRET)), clock=self.clock).poll()
        self.assertIn(CodexAuthError.user_message, state['error'])
        self.assertNotIn(SECRET, state['error'])

    def test_recovery_clears_error_and_stale(self):
        monitor = Monitor(ScriptedClient(self.good(), RuntimeError('x'), self.good()), clock=self.clock)
        monitor.poll()
        monitor.poll()
        self.clock.now += 60
        state = monitor.poll()
        self.assertTrue(state['live'])
        self.assertFalse(state['stale'])
        self.assertNotIn('error', state)
        self.assertEqual(state['updatedAt'], self.clock.now)

    def test_empty_response_is_a_failure_and_goes_stale(self):
        monitor = Monitor(ScriptedClient(self.good(), {}), clock=self.clock)
        monitor.poll()
        state = monitor.poll()
        self.assertTrue(state['stale'])
        self.assertTrue(state['error'])

    def test_credits_survive_a_response_without_windows(self):
        result = make_result(primary=None, credits={'balance': '1234.56789'})
        state = Monitor(ScriptedClient(result), clock=self.clock).poll()
        self.assertTrue(state['live'])
        self.assertEqual(state['windows'], [])
        self.assertEqual(state['credits']['balance'], '1234.56789')
        self.assertNotIn('error', state)

    def test_plan_falls_back_to_account_lookup(self):
        state = Monitor(ScriptedClient(self.good(), plan='pro'), clock=self.clock).poll()
        self.assertEqual(state['plan'], 'pro')
        state = Monitor(ScriptedClient(self.good(), plan=object()), clock=self.clock).poll()
        self.assertIsNone(state['plan'])


class Demo(unittest.TestCase):
    def test_demo_is_labelled_and_exercises_the_real_parser(self):
        clock = Clock()
        state = Monitor(DemoClient(clock), demo=True, clock=clock).poll()
        self.assertIs(state['demo'], True)
        self.assertTrue(state['live'])
        self.assertEqual(state['credits']['balance'], '1234.56789')
        self.assertEqual(len(state['windows']), 2)
        self.assertGreater(state['windows'][0]['reset'], clock.now)
        self.assertGreater(state['resetCreditExpiry'], clock.now)
        self.assertEqual(state['resetCredits'], 2)

    def test_real_data_is_never_labelled_demo(self):
        state = Monitor(ScriptedClient(make_result(credits={'balance': '1'}))).poll()
        self.assertNotIn('demo', state)


class WaitForRequest(unittest.TestCase):
    def setUp(self):
        self.read_fd, self.write_fd = os.pipe()
        self.open_fds = {self.read_fd, self.write_fd}
        self.addCleanup(lambda: [self.close(fd) for fd in list(self.open_fds)])

    def close(self, fd):
        self.open_fds.discard(fd)
        os.close(fd)

    def test_timeout(self):
        self.assertEqual(feed.wait_for_request(0.05, self.read_fd), 'timeout')

    def test_queued_requests_coalesce(self):
        os.write(self.write_fd, b'refresh\nrefresh\nrefresh\n')
        self.assertEqual(feed.wait_for_request(1, self.read_fd), 'refresh')
        self.assertEqual(feed.wait_for_request(0.05, self.read_fd), 'timeout')

    def test_closed_input_means_stop(self):
        self.close(self.write_fd)
        self.assertEqual(feed.wait_for_request(1, self.read_fd), 'eof')


def feed_environment(**extra):
    env = {key: value for key, value in os.environ.items()
           if key not in ('CODEX_HOME', 'CODEX_BIN', 'PYTHONPATH')}
    env['PYTHONPATH'] = str(ROOT / 'vendor')
    env.update(extra)
    return env


def run_feed(*args, **env):
    done = subprocess.run(
        [sys.executable, '-B', str(FEED_SCRIPT)] + list(args),
        env=feed_environment(**env), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=60)
    return done


class FeedScript(unittest.TestCase):
    """Runs Sources/feed.py as the app does, against a fake local app-server."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def fake(self, mode):
        return FakeCodex(tempfile.mkdtemp(dir=self.directory), mode)

    def test_once_prints_one_clean_snapshot(self):
        fake = self.fake('ok')
        done = run_feed('--once', CODEX_BIN=str(fake.executable))
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        state = json.loads(lines[0])
        self.assertTrue(state['live'])
        self.assertEqual(state['credits']['balance'], '1234.56789')
        self.assertEqual([w['label'] for w in state['windows']], ['5H', '7D'])
        self.assertEqual(state['resetCredits'], 2)
        self.assertEqual(state['plan'], 'plus')
        self.assertNotIn('example.invalid', done.stdout)  # account identity stays out of the feed

    def test_codex_home_override_reaches_codex(self):
        fake = self.fake('ok')
        home = self.directory / 'chosen-home'
        run_feed('--once', CODEX_BIN=str(fake.executable), CODEX_HOME=str(home))
        self.assertEqual(fake.starts()[0]['codex_home'], str(home))

    def test_codex_home_is_not_invented_when_unset(self):
        fake = self.fake('ok')
        run_feed('--once', CODEX_BIN=str(fake.executable))
        self.assertIsNone(fake.starts()[0]['codex_home'])

    def test_server_failure_is_reported_without_details(self):
        fake = self.fake('error')
        done = run_feed('--once', CODEX_BIN=str(fake.executable))
        state = json.loads(done.stdout)
        self.assertFalse(state['live'])
        self.assertTrue(state['error'])
        self.assertNotIn(SECRET, done.stdout)

    def test_missing_codex_reports_how_to_fix_it(self):
        done = run_feed('--once', CODEX_BIN=str(self.directory / 'absent'))
        state = json.loads(done.stdout)
        self.assertFalse(state['live'])
        self.assertIn('Install Codex', state['error'])
        self.assertNotIn(str(self.directory), done.stdout)

    def test_credits_are_shown_when_codex_reports_no_windows(self):
        fake = self.fake('credits_only')
        state = json.loads(run_feed('--once', CODEX_BIN=str(fake.executable)).stdout)
        self.assertTrue(state['live'])
        self.assertEqual(state['windows'], [])
        self.assertEqual(state['credits']['balance'], '7.25')

    def test_stale_then_recovered_across_server_restart(self):
        fake = self.fake('second_call_error')
        client = MonitorClient(executable=str(fake.executable))
        self.addCleanup(client.close)
        monitor = Monitor(client)
        first, second, third = monitor.poll(), monitor.poll(), monitor.poll()
        self.assertTrue(first['live'])
        self.assertTrue(second['stale'])
        self.assertEqual(second['credits'], first['credits'])
        self.assertNotIn(SECRET, json.dumps(second))
        self.assertTrue(third['live'])
        self.assertFalse(third['stale'])
        self.assertEqual(len(fake.starts()), 2)  # the failure forced a restart

    def test_demo_never_starts_codex(self):
        fake = self.fake('ok')
        done = run_feed('--once', '--demo', CODEX_BIN=str(fake.executable))
        state = json.loads(done.stdout)
        self.assertIs(state['demo'], True)
        self.assertEqual(state['credits']['balance'], '1234.56789')
        self.assertEqual(fake.entries(), [])

    def test_demo_loop_refreshes_on_request_and_stops_on_eof(self):
        process = subprocess.Popen(
            [sys.executable, '-B', str(FEED_SCRIPT), '--demo'], env=feed_environment(),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        watchdog = threading.Timer(30, process.kill)
        watchdog.start()
        try:
            first = json.loads(process.stdout.readline())
            process.stdin.write('refresh\n')
            process.stdin.flush()
            second = json.loads(process.stdout.readline())
            process.stdin.close()
            code = process.wait(timeout=20)
        finally:
            watchdog.cancel()
            process.kill()
            process.stdout.close()
        self.assertTrue(first['demo'] and second['demo'])
        self.assertGreaterEqual(second['updatedAt'], first['updatedAt'])
        self.assertEqual(code, 0)

    def test_sigterm_exits_cleanly(self):
        process = subprocess.Popen(
            [sys.executable, '-B', str(FEED_SCRIPT), '--demo'], env=feed_environment(),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        watchdog = threading.Timer(30, process.kill)
        watchdog.start()
        try:
            process.stdout.readline()
            process.terminate()
            code = process.wait(timeout=20)
        finally:
            watchdog.cancel()
            process.kill()
            process.stdin.close()
            process.stdout.close()
        self.assertEqual(code, 0)


class NoTimezoneDependency(unittest.TestCase):
    def test_feed_does_not_pin_a_timezone(self):
        source = FEED_SCRIPT.read_text()
        self.assertNotIn('zoneinfo', source)
        self.assertNotIn('Cairo', source)
        self.assertNotIn('Africa/', source)

    def test_snapshot_time_is_an_epoch_number(self):
        state = Monitor(DemoClient(), demo=True).poll()
        self.assertIsInstance(state['updatedAt'], int)
        self.assertTrue(math.isfinite(state['updatedAt']))


if __name__ == '__main__':
    unittest.main()
