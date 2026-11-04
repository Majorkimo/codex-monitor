"""The opt-in Claude status line installer, exercised only in temp directories."""
import importlib.util
import json
import os
import shlex
import stat
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import ROOT  # noqa: E402

_spec = importlib.util.spec_from_file_location('connect_claude', ROOT / 'Scripts' / 'connect_claude.py')
connect_claude = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(connect_claude)


class InstallerCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = Path(self._dir.name)
        self.settings = self.dir / 'claude' / 'settings.json'
        self.install = self.dir / 'share'
        self.lines = []

    def run_tool(self, *flags):
        self.lines = []
        code = connect_claude.main(['--settings', str(self.settings), '--install-dir', str(self.install), *flags],
                                   out=self.lines.append)
        return code

    def output(self):
        return '\n'.join(self.lines)

    def write_settings(self, data, mode=0o640):
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(data if isinstance(data, str) else json.dumps(data))
        self.settings.chmod(mode)

    def load(self):
        return json.loads(self.settings.read_text())

    def snapshot_tree(self):
        return sorted(str(p.relative_to(self.dir)) for p in self.dir.rglob('*'))


ORIGINAL = {'permissions': {'allow': ['Bash(ls)']}, 'hooks': {'Stop': []}, 'env': {'X': '1'}, 'telemetry': False}


class DryRun(InstallerCase):
    def test_default_writes_nothing_with_or_without_settings(self):
        self.assertEqual(self.run_tool(), 0)
        self.assertEqual(self.snapshot_tree(), [])
        self.write_settings(ORIGINAL)
        before = (self.settings.read_text(), self.snapshot_tree())
        self.assertEqual(self.run_tool(), 0)
        self.assertEqual((self.settings.read_text(), self.snapshot_tree()), before)
        self.assertIn('Dry run', self.output())

    def test_uninstall_default_is_also_dry_run(self):
        self.write_settings(ORIGINAL)
        self.run_tool('--apply')
        before = (self.settings.read_text(), self.snapshot_tree())
        self.assertEqual(self.run_tool('--uninstall'), 0)
        self.assertEqual((self.settings.read_text(), self.snapshot_tree()), before)


class Apply(InstallerCase):
    def test_adds_only_status_line_and_preserves_everything_else(self):
        self.write_settings(ORIGINAL)
        self.assertEqual(self.run_tool('--apply'), 0)
        data = self.load()
        self.assertEqual({k: v for k, v in data.items() if k != 'statusLine'}, ORIGINAL)
        script = self.install / 'claude_statusline.py'
        self.assertEqual(data['statusLine']['type'], 'command')
        self.assertEqual(shlex.split(data['statusLine']['command']), [sys.executable, str(script)])
        self.assertEqual(script.read_text(), (ROOT / 'Sources' / 'claude_statusline.py').read_text())
        self.assertEqual(stat.S_IMODE(self.settings.stat().st_mode), 0o640)  # original mode kept

    def test_backup_is_private_and_matches_original(self):
        self.write_settings(ORIGINAL)
        original = self.settings.read_text()
        self.run_tool('--apply')
        backups = list((self.install / 'backups').iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), original)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.install / 'claude-connect.json').stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.install.stat().st_mode), 0o700)

    def test_idempotent(self):
        self.write_settings(ORIGINAL)
        self.run_tool('--apply')
        state = (self.settings.read_text(), self.snapshot_tree())
        self.assertEqual(self.run_tool('--apply'), 0)
        self.assertIn('Already connected', self.output())
        self.assertEqual((self.settings.read_text(), self.snapshot_tree()), state)

    def test_existing_status_line_is_refused_and_untouched(self):
        mine = dict(ORIGINAL, statusLine={'type': 'command', 'command': 'echo hi'})
        self.write_settings(mine)
        before = self.settings.read_text()
        self.assertEqual(self.run_tool('--apply'), 2)
        self.assertEqual(self.settings.read_text(), before)
        self.assertEqual(self.snapshot_tree(), ['claude', 'claude/settings.json'])

    def test_invalid_json_non_object_and_symlink_are_refused(self):
        for text in ('{ not json', '[1, 2]', '"x"'):
            self.write_settings(text)
            self.assertEqual(self.run_tool('--apply'), 2, msg=text)
            self.assertEqual(self.settings.read_text(), text)
        self.assertFalse(self.install.exists())
        real = self.dir / 'real.json'
        real.write_text('{}')
        self.settings.unlink()
        self.settings.symlink_to(real)
        self.assertEqual(self.run_tool('--apply'), 2)
        self.assertEqual(real.read_text(), '{}')

    def test_creates_new_private_settings_when_none_exist(self):
        self.assertEqual(self.run_tool('--apply'), 0)
        self.assertEqual(list(self.load()), ['statusLine'])
        self.assertEqual(stat.S_IMODE(self.settings.stat().st_mode), 0o600)
        self.assertFalse((self.install / 'backups').exists())

    def test_backups_are_never_overwritten(self):
        self.write_settings(ORIGINAL)
        self.run_tool('--apply')
        first = next((self.install / 'backups').iterdir())
        marker = first.read_text()
        self.run_tool('--uninstall', '--apply')
        self.run_tool('--apply')
        self.assertEqual(first.read_text(), marker)


class Uninstall(InstallerCase):
    def test_round_trip_restores_original_settings(self):
        self.write_settings(ORIGINAL)
        self.run_tool('--apply')
        self.assertEqual(self.run_tool('--uninstall', '--apply'), 0)
        self.assertEqual(self.load(), ORIGINAL)
        self.assertFalse((self.install / 'claude_statusline.py').exists())
        self.assertFalse((self.install / 'claude-connect.json').exists())
        self.assertTrue((self.install / 'backups').exists())  # kept

    def test_later_unrelated_changes_are_preserved(self):
        self.write_settings(ORIGINAL)
        self.run_tool('--apply')
        data = self.load()
        data['model'] = 'later-choice'
        data['permissions']['allow'].append('Bash(pwd)')
        self.write_settings(data)
        self.run_tool('--uninstall', '--apply')
        expected = dict(ORIGINAL, model='later-choice')
        expected['permissions'] = {'allow': ['Bash(ls)', 'Bash(pwd)']}
        self.assertEqual(self.load(), expected)

    def test_conflict_when_status_line_was_changed(self):
        self.write_settings(ORIGINAL)
        self.run_tool('--apply')
        data = self.load()
        data['statusLine'] = {'type': 'command', 'command': 'echo mine'}
        self.write_settings(data)
        before = self.settings.read_text()
        self.assertEqual(self.run_tool('--uninstall', '--apply'), 2)
        self.assertIn('CONFLICT', self.output())
        self.assertEqual(self.settings.read_text(), before)
        self.assertTrue((self.install / 'claude-connect.json').exists())

    def test_conflict_when_status_line_was_removed_by_user(self):
        self.write_settings(ORIGINAL)
        self.run_tool('--apply')
        self.write_settings(ORIGINAL)
        self.assertEqual(self.run_tool('--uninstall', '--apply'), 2)
        self.assertEqual(self.load(), ORIGINAL)

    def test_nothing_to_undo_without_our_metadata(self):
        self.write_settings(dict(ORIGINAL, statusLine={'type': 'command', 'command': 'echo mine'}))
        before = self.settings.read_text()
        self.assertEqual(self.run_tool('--uninstall', '--apply'), 2)
        self.assertEqual(self.settings.read_text(), before)

    def test_no_prior_file_removes_only_our_key(self):
        self.run_tool('--apply')
        self.run_tool('--uninstall', '--apply')
        self.assertFalse(self.settings.exists())  # only our key was ever there
        self.run_tool('--apply')
        data = self.load()
        data['later'] = True
        self.write_settings(data)
        self.run_tool('--uninstall', '--apply')
        self.assertEqual(self.load(), {'later': True})


class Ownership(InstallerCase):
    def test_foreign_script_or_metadata_is_never_overwritten(self):
        self.write_settings(ORIGINAL)
        self.install.mkdir()
        for name in ('claude_statusline.py', 'claude-connect.json'):
            for flags in ((), ('--apply',)):
                path = self.install / name
                path.write_text('{"foreign": 1}')
                self.assertEqual(self.run_tool(*flags), 2, msg=name)
                self.assertEqual(path.read_text(), '{"foreign": 1}')
                self.assertEqual(self.load(), ORIGINAL)
                self.assertFalse((self.install / 'backups').exists())
                path.unlink()

    def test_foreign_script_with_foreign_metadata_for_other_dir_is_refused(self):
        self.write_settings(ORIGINAL)
        self.install.mkdir()
        (self.install / 'claude_statusline.py').write_text('mine')
        (self.install / 'claude-connect.json').write_text(json.dumps(
            {'version': 1, 'settings': str(self.settings), 'command': 'python /elsewhere/claude_statusline.py'}))
        self.assertEqual(self.run_tool('--apply'), 2)
        self.assertEqual((self.install / 'claude_statusline.py').read_text(), 'mine')

    def test_symlinked_install_dir_is_refused_for_every_mode(self):
        self.write_settings(ORIGINAL)
        real = self.dir / 'real-share'
        real.mkdir()
        (real / 'claude-connect.json').write_text('{"settings": "x"}')
        self.install.symlink_to(real)
        before = (self.settings.read_text(), sorted(p.name for p in real.iterdir()))
        for flags in ((), ('--apply',), ('--uninstall',), ('--uninstall', '--apply')):
            self.assertEqual(self.run_tool(*flags), 2, msg=flags)
            self.assertEqual((self.settings.read_text(), sorted(p.name for p in real.iterdir())), before)

    def test_symlinked_metadata_is_refused_without_following(self):
        self.write_settings(ORIGINAL)
        self.run_tool('--apply')
        meta = self.install / 'claude-connect.json'
        real = self.dir / 'meta-real.json'
        real.write_text(meta.read_text())
        meta.unlink()
        meta.symlink_to(real)
        before = self.settings.read_text()
        for flags in ((), ('--apply',), ('--uninstall',), ('--uninstall', '--apply')):
            self.assertEqual(self.run_tool(*flags), 2, msg=flags)
        self.assertEqual(self.settings.read_text(), before)
        self.assertTrue(meta.is_symlink())
        self.assertTrue((self.install / 'claude_statusline.py').exists())

    def test_uninstall_ignores_untrusted_metadata_command(self):
        self.write_settings(ORIGINAL)
        self.run_tool('--apply')
        meta = self.install / 'claude-connect.json'
        data = json.loads(meta.read_text())
        data['command'] = 'echo mine'
        meta.write_text(json.dumps(data))
        data = self.load()
        data['statusLine'] = {'type': 'command', 'command': 'echo mine'}
        self.write_settings(data)
        before = self.settings.read_text()
        self.assertEqual(self.run_tool('--uninstall', '--apply'), 2)
        self.assertEqual(self.settings.read_text(), before)
        self.assertTrue((self.install / 'claude_statusline.py').exists())


class ConcurrentChange(InstallerCase):
    def mutate_before_commit(self, change):
        real = connect_claude.ensure_unchanged

        def wrapper(path, raw):
            change()
            return real(path, raw)
        patcher = unittest.mock.patch.object(connect_claude, 'ensure_unchanged', wrapper)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_connect_refuses_when_settings_changed(self):
        self.write_settings(ORIGINAL, mode=0o640)
        changed = dict(ORIGINAL, model='concurrent')
        self.mutate_before_commit(lambda: self.write_settings(changed, mode=0o640))
        self.assertEqual(self.run_tool('--apply'), 2)
        self.assertIn('changed while', self.output())
        self.assertEqual(self.load(), changed)
        backups = list((self.install / 'backups').iterdir())
        self.assertEqual(len(backups), 1)  # backup of what was read is kept
        self.assertEqual(json.loads(backups[0].read_text()), ORIGINAL)

    def test_connect_refuses_when_settings_appear_or_vanish(self):
        self.mutate_before_commit(lambda: self.write_settings({'new': 1}))
        self.assertEqual(self.run_tool('--apply'), 2)
        self.assertEqual(self.load(), {'new': 1})

    def test_connect_refuses_when_settings_deleted(self):
        self.write_settings(ORIGINAL)
        self.mutate_before_commit(self.settings.unlink)
        self.assertEqual(self.run_tool('--apply'), 2)
        self.assertFalse(self.settings.exists())

    def test_uninstall_refuses_when_settings_changed_even_for_delete(self):
        self.assertEqual(self.run_tool('--apply'), 0)  # creates a new file holding only our key
        changed = dict(self.load(), later=True)
        self.mutate_before_commit(lambda: self.write_settings(changed))
        self.assertEqual(self.run_tool('--uninstall', '--apply'), 2)
        self.assertEqual(self.load(), changed)
        self.assertTrue((self.install / 'claude-connect.json').exists())
        self.assertTrue((self.install / 'claude_statusline.py').exists())

    def test_uninstall_refuses_when_other_key_changed(self):
        self.write_settings(ORIGINAL, mode=0o640)
        self.run_tool('--apply')
        changed = dict(self.load(), model='concurrent')
        self.mutate_before_commit(lambda: self.write_settings(changed, mode=0o640))
        self.assertEqual(self.run_tool('--uninstall', '--apply'), 2)
        self.assertEqual(self.load(), changed)
        self.assertEqual(stat.S_IMODE(self.settings.stat().st_mode), 0o640)


class Source(unittest.TestCase):
    def test_does_not_touch_real_home_by_default_paths(self):
        # The defaults only point inside the user's home; tests above always pass explicit paths.
        self.assertEqual(connect_claude.default_settings().name, 'settings.json')
        self.assertIn('codex-monitor', str(connect_claude.default_install_dir()))

    def test_only_status_line_key_is_written(self):
        source = (ROOT / 'Scripts' / 'connect_claude.py').read_text().lower()
        for forbidden in ('urllib', 'socket', 'subprocess', 'keychain', '.credentials', 'permissions', 'hooks'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
