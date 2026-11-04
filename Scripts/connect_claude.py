#!/usr/bin/env python3
"""Opt-in, reversible setup of the Claude Code status line for Codex Monitor.

Default is a dry run that writes nothing. With --apply it:
  * copies claude_statusline.py into the install directory,
  * adds ONE key, `statusLine`, to the Claude Code settings file,
  * keeps a private backup of the original settings and uninstall metadata.
It refuses to touch an existing `statusLine` (it never runs or wraps another
command) and changes no other key.

--uninstall (also a dry run unless --apply) removes the `statusLine` key only
while it still exactly matches what this script wrote. Other settings made
since, including edits to other keys, are left alone. If you changed
`statusLine` yourself, it reports a conflict and leaves everything in place.
If the settings file did not exist before, it is deleted on uninstall only
when nothing but our key was ever in it; otherwise it stays.
"""
import argparse
import json
import os
import shlex
import stat
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_NAME = 'claude_statusline.py'
METADATA_NAME = 'claude-connect.json'
MAX_SETTINGS_BYTES = 1024 * 1024
MAX_METADATA_BYTES = 64 * 1024


class Refused(Exception):
    pass


def default_settings():
    return Path(os.path.expanduser('~')) / '.claude' / 'settings.json'


def default_install_dir():
    return Path(os.path.expanduser('~')) / '.local' / 'share' / 'codex-monitor'


def find_statusline_source():
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / 'Sources' / SCRIPT_NAME, here / SCRIPT_NAME):
        if candidate.is_file():
            return candidate
    raise Refused('Cannot find %s next to this script.' % SCRIPT_NAME)


def read_settings_bytes(path):
    """Return the file's bytes, or None if absent. Refuses symlinks, non-files and oversized files."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode):
        raise Refused('%s is a symlink; refusing to modify it.' % path)
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SETTINGS_BYTES:
        raise Refused('%s is not a normal settings file of reasonable size.' % path)
    try:
        return Path(path).read_bytes()
    except OSError:
        raise Refused('%s could not be read. Nothing was changed.' % path)


def load_settings(path):
    """Return (raw bytes or None, dict). Refuses invalid JSON or a non-object."""
    raw = read_settings_bytes(path)
    if raw is None:
        return None, {}
    try:
        data = json.loads(raw.decode('utf-8'))
    except ValueError:
        raise Refused('%s is not valid JSON; fix or move it first. Nothing was changed.' % path)
    if not isinstance(data, dict):
        raise Refused('%s does not contain a JSON object. Nothing was changed.' % path)
    return raw, data


def ensure_unchanged(path, raw):
    """Refuse if the settings differ from the bytes this operation read.

    Narrows the window for lost updates; it is not an atomic compare-and-swap.
    """
    if read_settings_bytes(path) != raw:
        raise Refused('%s changed while this was running. Nothing was changed; run it again.' % path)


def atomic_write(path, text, mode):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            fd = None
            handle.write(text)
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


def settings_text(data):
    return json.dumps(data, indent=2, ensure_ascii=False) + '\n'


def status_line_entry(script):
    return {'type': 'command', 'command': '%s %s' % (shlex.quote(sys.executable), shlex.quote(str(script)))}


def private_dir(path):
    path = Path(path)
    if path.is_symlink():
        raise Refused('%s is a symlink; refusing to use it.' % path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def make_backup(raw, install_dir):
    """Save the original settings bytes to a new private file; never overwrites an older backup."""
    backups = private_dir(Path(install_dir) / 'backups')
    stamp = time.strftime('%Y%m%d-%H%M%S')
    for attempt in range(1000):
        target = backups / ('settings-%s-%d.json' % (stamp, attempt))
        try:
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
        return target
    raise Refused('Could not find a free backup name; clean up %s.' % backups)


def check_install_dir(install_dir):
    """Refuse a symlinked or non-directory install dir before anything inside it is read."""
    try:
        info = os.lstat(install_dir)
    except (FileNotFoundError, NotADirectoryError):
        return
    if stat.S_ISLNK(info.st_mode):
        raise Refused('%s is a symlink; refusing to use it.' % install_dir)
    if not stat.S_ISDIR(info.st_mode):
        raise Refused('%s is not a directory.' % install_dir)


def read_metadata(install_dir):
    """Return our valid ownership metadata, None if absent; refuse anything else at that path."""
    path = Path(install_dir) / METADATA_NAME
    try:
        info = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_METADATA_BYTES:
        raise Refused('%s is not a regular metadata file; refusing to touch it.' % path)
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as handle:
            data = json.loads(handle.read(MAX_METADATA_BYTES + 1).decode('utf-8'))
    except (OSError, ValueError):
        data = None
    if not (isinstance(data, dict) and data.get('version') == 1
            and isinstance(data.get('settings'), str) and isinstance(data.get('command'), str)):
        raise Refused('%s exists but was not written by this installer; refusing to touch it.' % path)
    return data


def owns_script(metadata, install_dir):
    """True only if metadata names exactly our script inside this install dir."""
    try:
        parts = shlex.split(metadata['command'])
    except ValueError:
        return False
    return len(parts) == 2 and parts[1] == str(Path(install_dir) / SCRIPT_NAME)


def connect(settings, install_dir, apply, out):
    settings, install_dir = Path(settings), Path(install_dir)
    check_install_dir(install_dir)
    raw, data = load_settings(settings)
    existed = raw is not None
    source = find_statusline_source()
    target = install_dir / SCRIPT_NAME
    entry = status_line_entry(target)
    metadata = read_metadata(install_dir)

    if 'statusLine' in data:
        if data['statusLine'] == entry and metadata and metadata.get('settings') == str(settings):
            out('Already connected; nothing to change.')
            return 0
        if data['statusLine'] != entry:
            out('%s already has a statusLine. Not changing it (it is never run or wrapped).' % settings)
            out('Remove or replace it yourself if you want Codex Monitor to use the status line.')
            return 2
    if metadata and metadata.get('settings') != str(settings):
        out('This install directory is already connected to another settings file. Uninstall that first.')
        return 2
    if os.path.lexists(target) and not (metadata and owns_script(metadata, install_dir)):
        raise Refused('%s already exists and was not installed by this tool; leaving it in place.' % target)

    out('Plan:')
    out('  copy %s -> %s' % (SCRIPT_NAME, target))
    out('  set "statusLine" in %s%s' % (settings, '' if existed else ' (new file)'))
    out('  command: %s' % entry['command'])
    if existed:
        out('  back up the original settings under %s' % (install_dir / 'backups'))
    if not apply:
        out('Dry run: nothing was written. Re-run with --apply to proceed.')
        return 0

    private_dir(install_dir)
    if target.is_symlink():
        raise Refused('%s is a symlink; refusing to overwrite it.' % target)
    backup = make_backup(raw, install_dir) if existed else None
    if not (metadata and data.get('statusLine') == entry):
        metadata = {'version': 1, 'settings': str(settings), 'settingsExisted': existed,
                    'command': entry['command'], 'backup': str(backup) if backup else None}
        atomic_write(install_dir / METADATA_NAME, json.dumps(metadata, indent=2) + '\n', 0o600)
    atomic_write(target, source.read_text(encoding='utf-8'), 0o600)
    updated = dict(data)
    updated['statusLine'] = entry
    text = settings_text(updated)
    ensure_unchanged(settings, raw)
    mode = stat.S_IMODE(os.stat(settings).st_mode) if existed else 0o600
    atomic_write(settings, text, mode)
    out('Connected. Restart Claude Code sessions to pick it up; Codex Monitor reads the snapshot it writes.')
    return 0


def uninstall(settings, install_dir, apply, out):
    settings, install_dir = Path(settings), Path(install_dir)
    check_install_dir(install_dir)
    metadata = read_metadata(install_dir)
    if not metadata or metadata.get('settings') != str(settings) or not owns_script(metadata, install_dir):
        out('No connection by this installer was found for %s; nothing to undo.' % settings)
        return 2
    raw, data = load_settings(settings)
    expected = {'type': 'command', 'command': metadata.get('command')}
    if data.get('statusLine') != expected:
        out('CONFLICT: statusLine in %s is no longer the one this installer wrote.' % settings)
        out('Nothing was changed. Edit or remove it yourself; the metadata is kept at %s.' % (install_dir / METADATA_NAME))
        return 2
    remaining = {key: value for key, value in data.items() if key != 'statusLine'}
    delete_file = not metadata.get('settingsExisted') and not remaining
    out('Plan: remove only "statusLine" from %s%s; remove the copied script and metadata.'
        % (settings, ' (and delete the file, which this installer created and is now empty)' if delete_file else ''))
    out('Backups under %s are kept.' % (install_dir / 'backups'))
    if not apply:
        out('Dry run: nothing was written. Re-run with --uninstall --apply to proceed.')
        return 0
    text = settings_text(remaining)
    ensure_unchanged(settings, raw)
    if delete_file:
        settings.unlink()
    else:
        mode = stat.S_IMODE(os.stat(settings).st_mode)
        atomic_write(settings, text, mode)
    for name in (SCRIPT_NAME, METADATA_NAME):
        try:
            (install_dir / name).unlink()
        except OSError:
            pass
    out('Disconnected.')
    return 0


def main(argv=None, out=print):
    parser = argparse.ArgumentParser(description='Connect or disconnect the Claude Code status line (dry run by default).')
    parser.add_argument('--apply', action='store_true', help='actually make the change')
    parser.add_argument('--uninstall', action='store_true', help='undo a previous --apply')
    parser.add_argument('--settings', default=str(default_settings()), help='Claude Code settings.json')
    parser.add_argument('--install-dir', default=str(default_install_dir()), help='where the status line script is kept')
    args = parser.parse_args(argv)
    action = uninstall if args.uninstall else connect
    try:
        return action(args.settings, args.install_dir, args.apply, out)
    except Refused as exc:
        out(str(exc))
        return 2
    except OSError as exc:
        out('Could not complete (%s). Settings were not partially rewritten.' % exc.__class__.__name__)
        return 1


if __name__ == '__main__':
    sys.exit(main())
