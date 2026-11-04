"""Shared test helpers: import paths and an executable fake `codex` CLI."""
import json
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / 'Sources', ROOT / 'vendor'):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

FAKE_SERVER = Path(__file__).resolve().parent / 'fake_app_server.py'


class FakeCodex(object):
    """A `codex` executable that runs fake_app_server.py in the given mode."""

    def __init__(self, directory, mode):
        directory = Path(directory)
        self.log = directory / 'server.log'
        self.executable = directory / 'codex'
        self.executable.write_text('#!/bin/sh\nexec %s -I %s %s %s "$@"\n' % (
            shlex.quote(sys.executable), shlex.quote(str(FAKE_SERVER)),
            shlex.quote(mode), shlex.quote(str(self.log))))
        self.executable.chmod(0o755)

    def entries(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def starts(self):
        return [entry for entry in self.entries() if entry['event'] == 'start']

    def methods(self):
        return [entry['method'] for entry in self.entries() if entry['event'] == 'message']
