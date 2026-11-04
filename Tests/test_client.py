"""MonitorClient against a fake local app-server subprocess (no auth, no network)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import FakeCodex, ROOT  # noqa: E402  (also sets up import paths)

from divoom_minitoo_codex.appserver import AppServerError  # noqa: E402
from monitor_client import CodexAuthError, CodexStartError, MonitorClient  # noqa: E402


class ClientCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.clients = []
        self.addCleanup(lambda: [client.close() for client in self.clients])

    def client(self, mode, **options):
        fake = FakeCodex(tempfile.mkdtemp(dir=self.directory), mode)  # fresh log and counters
        client = MonitorClient(executable=str(fake.executable), **options)
        self.clients.append(client)
        return fake, client


class Protocol(ClientCase):
    def test_framing_survives_noise_split_and_foreign_replies(self):
        fake, client = self.client('ok')
        result = client.read_rate_limits()
        self.assertEqual(result['rateLimitsByLimitId']['codex']['credits']['balance'], '1234.56789')
        self.assertEqual(fake.methods(), ['initialize', 'initialized', 'account/rateLimits/read'])
        self.assertEqual(fake.starts()[0]['argv'], ['app-server', '--stdio'])

    def test_identifies_as_codex_monitor_not_divoom(self):
        fake, client = self.client('ok')
        client.read_rate_limits()
        info = next(entry['params']['clientInfo'] for entry in fake.entries()
                    if entry.get('method') == 'initialize')
        self.assertEqual(info['version'], (ROOT / 'VERSION').read_text().strip())
        for value in info.values():
            self.assertNotIn('divoom', value.lower())

    def test_only_read_only_account_methods_are_used(self):
        fake, client = self.client('ok')
        client.read_rate_limits()
        client.read_plan()
        allowed = {'initialize', 'initialized', 'account/rateLimits/read', 'account/read'}
        self.assertLessEqual(set(fake.methods()), allowed)
        refresh = [e['params'] for e in fake.entries() if e.get('method') == 'account/read']
        self.assertEqual(refresh, [{'refreshToken': False}])

    def test_codex_home_override_is_passed_through(self):
        home = self.directory / 'custom-home'
        fake, client = self.client('ok', codex_home=home)
        client.read_rate_limits()
        self.assertEqual(fake.starts()[0]['codex_home'], str(home))

    def test_plan_lookup_does_not_expose_identity(self):
        _, client = self.client('ok')
        self.assertEqual(client.read_plan(), 'plus')

    def test_unauthorized_becomes_fixed_auth_message(self):
        _, client = self.client('unauthorized')
        with self.assertRaises(CodexAuthError) as caught:
            client.read_rate_limits()
        self.assertNotIn('example.invalid', caught.exception.user_message)
        self.assertNotIn('example.invalid', str(caught.exception))

    def test_missing_executable_is_a_start_error(self):
        client = MonitorClient(executable=str(self.directory / 'does-not-exist'))
        with self.assertRaises(CodexStartError):
            client.read_rate_limits()
        self.assertIsNone(client.process)

    def test_silent_server_times_out_and_can_be_closed(self):
        _, client = self.client('hang', timeout=2)
        with self.assertRaises(AppServerError):
            client.read_rate_limits()
        client.close()
        self.assertIsNone(client.process)


class Lifecycle(ClientCase):
    def test_close_terminates_server_and_closes_pipes(self):
        _, client = self.client('ok')
        client.read_rate_limits()
        process = client.process
        client.close()
        self.assertIsNotNone(process.poll())
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        client.close()  # idempotent

    def test_server_exit_raises_and_next_call_launches_a_fresh_server(self):
        for mode in ('exit_immediately', 'die_after_init'):
            with self.subTest(mode=mode):
                fake, client = self.client(mode)
                for _attempt in range(2):
                    with self.assertRaises(AppServerError):
                        client.read_rate_limits()
                self.assertEqual(len(fake.starts()), 2)

    def test_server_killed_between_polls_is_restarted(self):
        fake, client = self.client('ok')
        client.read_rate_limits()
        client.process.kill()
        client.process.wait()
        result = client.read_rate_limits()
        self.assertIn('rateLimitsByLimitId', result)
        self.assertEqual(len(fake.starts()), 2)

    def test_restart_discards_partial_output_from_previous_server(self):
        # The first server dies after writing half a line. If that fragment
        # were kept, it would corrupt the next server's initialize reply.
        fake, client = self.client('partial_then_exit', timeout=3)
        with self.assertRaises(AppServerError):
            client.read_rate_limits()
        result = client.read_rate_limits()
        self.assertIn('rateLimitsByLimitId', result)
        self.assertEqual(len(fake.starts()), 2)

    def test_context_manager_cleans_up(self):
        fake = FakeCodex(self.directory, 'ok')
        with MonitorClient(executable=str(fake.executable)) as client:
            process = client.process
            self.assertIsNone(process.poll())
        self.assertIsNotNone(process.poll())


if __name__ == '__main__':
    unittest.main()
