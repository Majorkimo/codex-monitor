"""Claude usage sources, statusline writer, and provider-selected feed.

Everything is synthetic and runs in temporary directories: no real Claude
config, network, or sign-in is touched.
"""
import json
import math
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import ROOT  # noqa: E402

import claude_statusline  # noqa: E402
import claude_usage  # noqa: E402
from claude_usage import ClaudeUnavailable, read_usage  # noqa: E402
from feed import ClaudeMonitor, parse_provider  # noqa: E402

NOW = 1791000000
ACCOUNT = 'synthetic-account-0001'
PRIVATE = 'SYNTHETIC-PRIVATE-VALUE'


def snapshot_doc(received=NOW - 10, five=(37, NOW + 7200), seven=(62.4, NOW + 3 * 86400)):
    doc = {'version': 1, 'received_at': received}
    if five:
        doc['five_hour'] = {'used_percentage': five[0], 'resets_at': five[1]}
    if seven:
        doc['seven_day'] = {'used_percentage': seven[0], 'resets_at': seven[1]}
    return doc


def config_doc(fetched_ms=(NOW - 20) * 1000, cached=ACCOUNT, account=ACCOUNT,
               five=(12.5, '2026-10-14T22:00:00.123456+00:00'), seven=(40, '2026-10-20T10:00:00Z')):
    utilization = {'extra': {'utilization': 99, 'resets_at': None}}
    if five:
        utilization['five_hour'] = {'utilization': five[0], 'resets_at': five[1]}
    if seven:
        utilization['seven_day'] = {'utilization': seven[0], 'resets_at': seven[1]}
    return {
        'oauthAccount': {'accountUuid': account, 'emailAddress': PRIVATE},
        'projects': {PRIVATE: {}},
        'cachedUsageUtilization': {'fetchedAtMs': fetched_ms, 'accountUuid': cached, 'utilization': utilization},
    }


class TempCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = Path(self._dir.name)
        self.snapshot = self.dir / 'usage.json'
        self.config = self.dir / 'claude.json'
        self.env = {'CODEX_MONITOR_CLAUDE_SNAPSHOT': str(self.snapshot),
                    'CODEX_MONITOR_CLAUDE_CONFIG': str(self.config)}

    def put(self, path, doc, mode=0o600):
        path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
        path.chmod(mode)

    def read(self, now=NOW):
        return read_usage(now, self.env)

    def unavailable(self, now=NOW):
        with self.assertRaises(ClaudeUnavailable) as caught:
            self.read(now)
        return caught.exception.user_message


class SnapshotSource(TempCase):
    def test_reads_both_windows(self):
        self.put(self.snapshot, snapshot_doc())
        data = self.read()
        self.assertEqual([w['id'] for w in data['windows']], ['five_hour', 'seven_day'])
        self.assertEqual([w['label'] for w in data['windows']], ['5H', '7D'])
        self.assertEqual([w['used'] for w in data['windows']], [37, 62])
        self.assertEqual(data['observedAt'], NOW - 10)
        self.assertEqual(data['sourceLabel'], 'Statusline snapshot')
        self.assertFalse(data['stale'])

    def test_single_window_is_fine_and_missing_one_is_not_zero(self):
        self.put(self.snapshot, snapshot_doc(seven=None))
        self.assertEqual([w['id'] for w in self.read()['windows']], ['five_hour'])

    def test_no_windows_is_unavailable_with_guidance(self):
        self.put(self.snapshot, snapshot_doc(five=None, seven=None))
        self.assertIn('/usage', self.unavailable())

    def test_missing_everything_explains_setup(self):
        self.assertIn('Open /usage in Claude Code, or connect the status line; then refresh.', self.unavailable())

    def test_expired_window_is_dropped_not_zeroed(self):
        self.put(self.snapshot, snapshot_doc(five=(90, NOW - 1)))
        self.assertEqual([w['id'] for w in self.read()['windows']], ['seven_day'])
        self.put(self.snapshot, snapshot_doc(five=(90, NOW - 1), seven=(5, NOW - 100000)))
        self.assertIn('expired', self.unavailable())

    def test_stale_after_five_minutes_keeps_original_time(self):
        self.put(self.snapshot, snapshot_doc(received=NOW - 301))
        data = self.read()
        self.assertTrue(data['stale'])
        self.assertEqual(data['observedAt'], NOW - 301)
        self.put(self.snapshot, snapshot_doc(received=NOW - 300))
        self.assertFalse(self.read()['stale'])

    def test_rejects_bool_nonfinite_and_out_of_range(self):
        for bad in (True, False, float('nan'), float('inf'), -1, 100.5, '40', None):
            self.put(self.snapshot, snapshot_doc(five=(bad, NOW + 100), seven=None))
            with self.assertRaises(ClaudeUnavailable, msg=repr(bad)):
                self.read()
        for bad in (True, float('nan'), 0, -5, 'soon', 10 ** 12):
            self.put(self.snapshot, snapshot_doc(five=(10, bad), seven=None))
            with self.assertRaises(ClaudeUnavailable, msg=repr(bad)):
                self.read()

    def test_bad_version_future_time_and_corruption(self):
        for doc in ({'version': True, 'received_at': NOW}, {'version': 2, 'received_at': NOW},
                    snapshot_doc(received=NOW + 100000), snapshot_doc(received=True), [], 'not json', '{"version": 1'):
            self.put(self.snapshot, doc)
            self.assertIn('unreadable', self.unavailable())

    def test_corrupt_official_snapshot_never_falls_back_to_cache(self):
        self.put(self.config, config_doc(five=(12, f'{claude_time(NOW + 3600)}')))
        self.put(self.snapshot, 'garbage')
        self.assertIn('unreadable', self.unavailable())

    def test_present_but_invalid_snapshot_never_falls_back_to_cache(self):
        self.put(self.config, config_doc(five=(12, claude_time(NOW + 3600))))
        self.assertEqual(self.read()['source'], 'cache')  # the fallback itself is valid
        for text in ('null', '[]', '{}', '"x"', '7', json.dumps({'version': 2, 'received_at': NOW}),
                     json.dumps({'version': True, 'received_at': NOW}), '{"version": 1'):
            self.put(self.snapshot, text)
            self.assertIn('unreadable', self.unavailable(), msg=text)

    def test_oversized_world_writable_and_symlink_snapshots_rejected(self):
        self.put(self.snapshot, ' ' * 5000 + json.dumps(snapshot_doc()))
        self.assertIn('unreadable', self.unavailable())
        self.put(self.snapshot, snapshot_doc(), mode=0o666)
        self.assertIn('unreadable', self.unavailable())
        real = self.dir / 'real.json'
        self.put(real, snapshot_doc())
        self.snapshot.unlink()
        self.snapshot.symlink_to(real)
        self.assertIn('unreadable', self.unavailable())


def claude_time(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+00:00')


class CacheFallback(TempCase):
    def cache(self, **kwargs):
        kwargs.setdefault('five', (12.5, claude_time(NOW + 3600) + ''))
        kwargs.setdefault('seven', (40, claude_time(NOW + 86400).replace('+00:00', 'Z')))
        self.put(self.config, config_doc(**kwargs))

    def test_reads_matching_cache(self):
        self.cache()
        data = self.read()
        self.assertEqual([w['used'] for w in data['windows']], [13, 40])
        self.assertEqual(data['windows'][0]['reset'], NOW + 3600)
        self.assertEqual(data['windows'][1]['reset'], NOW + 86400)
        self.assertEqual(data['sourceLabel'], 'Claude Code cache')
        self.assertEqual(data['observedAt'], NOW - 20)

    def test_offsets_and_fractions(self):
        self.cache(five=(1, '2026-10-14T12:00:00.5-05:00'), seven=None)
        # 12:00 at UTC-5 is 17:00Z; fractional seconds are ignored.
        self.assertEqual(self.read(now=1791900000)['windows'][0]['reset'], 1791997200)

    def test_cross_account_and_empty_ids_rejected(self):
        for cached, account in ((ACCOUNT, 'other'), ('', ''), (ACCOUNT, None), (None, None), (ACCOUNT, ACCOUNT + ' ')):
            self.cache(cached=cached, account=account)
            self.assertIn('another account', self.unavailable(), msg=repr((cached, account)))

    def test_bad_timestamps_and_formats_are_unavailable(self):
        for kwargs in ({'fetched_ms': True}, {'fetched_ms': float('nan')}, {'fetched_ms': (NOW + 10 ** 6) * 1000},
                       {'five': (True, claude_time(NOW + 60)), 'seven': None},
                       {'five': (10, '2026-10-14 22:00:00'), 'seven': None},
                       {'five': (10, 12345), 'seven': None},
                       {'five': (float('inf'), claude_time(NOW + 60)), 'seven': None}):
            self.cache(**kwargs)
            with self.assertRaises(ClaudeUnavailable, msg=repr(kwargs)):
                self.read()

    def test_huge_timestamp_integers_do_not_overflow(self):
        for huge in (10 ** 400, -10 ** 400, 10 ** 30, 0, -1):
            self.cache(fetched_ms=huge)
            self.assertIn('another account', self.unavailable(), msg=repr(huge))

    def test_invalid_offsets_are_rejected(self):
        for offset in ('+01:99', '-00:60', '+24:00', '+99:00'):
            self.assertIsNone(claude_usage._iso_epoch('2026-10-14T12:00:00' + offset), msg=offset)
        self.assertIsNotNone(claude_usage._iso_epoch('2026-10-14T12:00:00+23:59'))
        self.cache(five=(10, '2026-10-14T12:00:00+01:99'), seven=None)
        with self.assertRaises(ClaudeUnavailable):
            self.read()

    def test_expired_and_stale_cache(self):
        self.cache(five=(50, claude_time(NOW - 5)), seven=None)
        self.assertIn('expired', self.unavailable())
        self.cache(fetched_ms=(NOW - 4000) * 1000)
        data = self.read()
        self.assertTrue(data['stale'])
        self.assertEqual(data['observedAt'], NOW - 4000)

    def test_invalid_oversized_or_unrelated_config(self):
        self.put(self.config, '{ nope')
        self.assertIn('cache', self.unavailable())
        self.put(self.config, {'projects': {}})
        self.assertIn('cache', self.unavailable())

    def test_no_raw_identity_or_private_field_in_result(self):
        self.cache()
        text = json.dumps(self.read()) + json.dumps(ClaudeMonitor(lambda now: read_usage(now, self.env)).poll())
        for secret in (ACCOUNT, PRIVATE, 'oauthAccount', 'projects', 'extra'):
            self.assertNotIn(secret, text)
        self.cache(account='other')
        text = json.dumps(ClaudeMonitor(lambda now: read_usage(now, self.env)).poll())
        self.assertNotIn(ACCOUNT, text)
        self.assertNotIn('other', text.replace('another', ''))


class Statusline(TempCase):
    def stdin(self, **limits):
        return json.dumps({'session_id': PRIVATE, 'workspace': {'current_dir': PRIVATE},
                           'model': {'display_name': PRIVATE}, 'rate_limits': limits}).encode()

    def run_line(self, raw):
        return claude_statusline.run(raw, self.env, NOW)

    def test_extracts_only_the_two_windows(self):
        raw = self.stdin(five_hour={'used_percentage': 23.5, 'resets_at': NOW + 100, 'extra': PRIVATE},
                         seven_day={'used_percentage': 41, 'resets_at': NOW + 5000})
        line = self.run_line(raw)
        self.assertEqual(line, 'Claude 5h 24% | 7d 41%')
        text = self.snapshot.read_text()
        self.assertEqual(json.loads(text), {
            'version': 1, 'received_at': NOW,
            'five_hour': {'used_percentage': 23.5, 'resets_at': NOW + 100},
            'seven_day': {'used_percentage': 41, 'resets_at': NOW + 5000}})
        self.assertNotIn(PRIVATE, text + line)

    def test_written_snapshot_is_readable_by_the_monitor(self):
        self.run_line(self.stdin(five_hour={'used_percentage': 5, 'resets_at': NOW + 100}))
        self.assertEqual(self.read()['windows'][0]['used'], 5)

    def test_file_is_private_and_parent_created_0700(self):
        target = self.dir / 'new' / 'claude-usage.json'
        env = {'CODEX_MONITOR_CLAUDE_SNAPSHOT': str(target)}
        claude_statusline.run(self.stdin(five_hour={'used_percentage': 1, 'resets_at': NOW + 5}), env, NOW)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(target.parent.stat().st_mode), 0o700)
        self.assertEqual([p.name for p in target.parent.iterdir()], ['claude-usage.json'])  # no temp leftovers

    def test_missing_or_malformed_windows_clear_old_data(self):
        self.run_line(self.stdin(five_hour={'used_percentage': 50, 'resets_at': NOW + 100}))
        self.assertIn('five_hour', self.snapshot.read_text())
        cases = [b'{}', b'', b'not json', b'[]', self.stdin(), b'x' * 10,
                 self.stdin(five_hour={'used_percentage': True, 'resets_at': NOW + 100}),
                 self.stdin(five_hour={'used_percentage': 10, 'resets_at': NOW - 1}),
                 self.stdin(five_hour={'used_percentage': 101, 'resets_at': NOW + 5}),
                 b'{"rate_limits":{"five_hour":{"used_percentage":NaN,"resets_at":Infinity}}}']
        for raw in cases:
            self.run_line(self.stdin(five_hour={'used_percentage': 50, 'resets_at': NOW + 100}))
            line = self.run_line(raw)
            self.assertEqual(json.loads(self.snapshot.read_text()), {'version': 1, 'received_at': NOW}, msg=raw)
            self.assertEqual(line, 'Claude usage n/a')

    def test_one_missing_window_drops_only_that_window(self):
        self.run_line(self.stdin(seven_day={'used_percentage': 9, 'resets_at': NOW + 100}))
        self.assertNotIn('five_hour', json.loads(self.snapshot.read_text()))

    def test_each_window_is_validated_independently(self):
        good_five = {'used_percentage': 10, 'resets_at': NOW + 100}
        good_seven = {'used_percentage': 20, 'resets_at': NOW + 200}
        bad = [None, 'x', 5, [], {}, {'used_percentage': True, 'resets_at': NOW + 1},
               {'used_percentage': 5, 'resets_at': NOW - 1}, {'used_percentage': 'a', 'resets_at': 'b'}]
        absent = object()
        for five in [absent, *bad, good_five]:
            for seven in [absent, *bad, good_seven]:
                limits = {}
                if five is not absent:
                    limits['five_hour'] = five
                if seven is not absent:
                    limits['seven_day'] = seven
                snapshot = claude_statusline.build_snapshot(self.stdin(**limits), NOW)
                expected = {'version': 1, 'received_at': NOW}
                if five is good_five:
                    expected['five_hour'] = good_five
                if seven is good_seven:
                    expected['seven_day'] = good_seven
                self.assertEqual(snapshot, expected, msg=repr((five, seven)))

    def test_bad_containers_give_empty_snapshot(self):
        for raw in (b'null', b'[]', b'{"rate_limits": null}', b'{"rate_limits": []}', b'{"rate_limits": "x"}'):
            self.assertEqual(claude_statusline.build_snapshot(raw, NOW), {'version': 1, 'received_at': NOW})

    def test_write_failure_is_silent(self):
        env = {'CODEX_MONITOR_CLAUDE_SNAPSHOT': str(self.dir / 'file' / 'x.json')}
        (self.dir / 'file').write_text('not a directory')
        self.assertEqual(claude_statusline.run(self.stdin(), env, NOW), 'Claude usage n/a')

    def test_script_runs_standalone_and_bounds_input(self):
        script = ROOT / 'Sources' / 'claude_statusline.py'
        env = {'CODEX_MONITOR_CLAUDE_SNAPSHOT': str(self.snapshot), 'HOME': str(self.dir)}
        future = int(claude_statusline.time.time()) + 1000
        raw = self.stdin(five_hour={'used_percentage': 12, 'resets_at': future})
        done = subprocess.run([sys.executable, '-I', str(script)], input=raw, env=env, capture_output=True, timeout=30)
        self.assertEqual((done.returncode, done.stdout.decode().strip(), done.stderr), (0, 'Claude 5h 12%', b''))
        big = b'{"pad":"' + b'x' * (2 * 1024 * 1024) + b'"}'
        done = subprocess.run([sys.executable, '-I', str(script)], input=big, env=env, capture_output=True, timeout=30)
        self.assertEqual((done.returncode, done.stderr), (0, b''))
        self.assertNotIn('five_hour', self.snapshot.read_text())

    def test_no_network_or_subprocess_imports(self):
        source = (ROOT / 'Sources' / 'claude_statusline.py').read_text()
        for name in ('socket', 'urllib', 'http', 'subprocess', 'requests', 'threading', 'sched'):
            self.assertNotIn('import ' + name, source)


class ProviderFeed(TempCase):
    def run_feed(self, *args, extra=None):
        env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(self.dir), 'CODEX_BIN': str(self.dir / 'no-such-codex'),
               'PYTHONPATH': str(ROOT / 'vendor'), **self.env, **(extra or {})}
        done = subprocess.run([sys.executable, '-B', str(ROOT / 'Sources' / 'feed.py'), '--once', *args],
                              capture_output=True, env=env, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.decode().splitlines()[0])

    def test_parse_provider(self):
        self.assertEqual(parse_provider([]), 'codex')
        self.assertEqual(parse_provider(['--provider', 'claude']), 'claude')
        self.assertEqual(parse_provider(['--provider=codex']), 'codex')
        self.assertIsNone(parse_provider(['--provider', 'other']))
        self.assertIsNone(parse_provider(['--provider']))

    def test_claude_helper_reads_snapshot_and_never_starts_codex(self):
        future = int(claude_statusline.time.time()) + 3600
        self.put(self.snapshot, snapshot_doc(received=int(claude_statusline.time.time()), five=(20, future), seven=None))
        state = self.run_feed('--provider', 'claude')
        self.assertEqual(state['provider'], 'claude')
        self.assertTrue(state['available'])
        self.assertEqual(state['windows'][0]['used'], 20)
        for key in ('credits', 'resetCredits', 'resetCreditExpiry', 'balance'):
            self.assertNotIn(key, state)
        self.assertFalse(state['live'])

    def test_claude_unavailable_has_no_windows_and_no_codex_values(self):
        state = self.run_feed('--provider', 'claude')
        self.assertFalse(state['available'])
        self.assertEqual(state['windows'], [])
        self.assertIn('/usage', state['notice'])
        self.assertNotIn('credits', state)

    def test_demo_for_both_providers_is_synthetic(self):
        codex = self.run_feed('--demo')
        self.assertEqual(codex['provider'], 'codex')
        self.assertTrue(codex['demo'])
        self.assertIn('credits', codex)
        claude = self.run_feed('--provider', 'claude', '--demo')
        self.assertEqual((claude['provider'], claude['demo'], claude['sourceLabel']), ('claude', True, 'Demo data'))
        self.assertEqual(len(claude['windows']), 2)
        self.assertNotIn('credits', claude)

    def test_demo_claude_ignores_real_snapshot(self):
        self.put(self.snapshot, snapshot_doc(received=int(claude_statusline.time.time())))
        self.assertEqual(self.run_feed('--provider', 'claude', '--demo')['source'], 'demo')

    def test_unknown_provider_is_a_fatal_message(self):
        env = {'PATH': os.environ.get('PATH', '')}
        done = subprocess.run([sys.executable, '-B', str(ROOT / 'Sources' / 'feed.py'), '--provider', 'nope'],
                              capture_output=True, env=env, timeout=30)
        self.assertEqual(done.returncode, 1)
        self.assertTrue(json.loads(done.stdout.decode())['fatal'])

    def test_monitor_keeps_no_state_between_polls(self):
        readings = [{'windows': [{'id': 'five_hour', 'label': '5H', 'used': 5, 'reset': NOW + 1}], 'source': 'statusline',
                     'sourceLabel': 'Statusline snapshot', 'observedAt': NOW, 'stale': False}, ClaudeUnavailable('Fixed.')]

        def reader(_now):
            item = readings.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        monitor = ClaudeMonitor(reader, clock=lambda: NOW)
        self.assertTrue(monitor.poll()['available'])
        second = monitor.poll()
        self.assertEqual((second['available'], second['windows'], second['notice']), (False, [], 'Fixed.'))

    def test_unexpected_reader_failure_uses_fixed_message(self):
        def reader(_now):
            raise RuntimeError(PRIVATE)
        state = ClaudeMonitor(reader, clock=lambda: NOW).poll()
        self.assertNotIn(PRIVATE, json.dumps(state))
        self.assertFalse(state['available'])


if __name__ == '__main__':
    unittest.main()
