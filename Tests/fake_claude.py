"""Synthetic stand-in for the Claude Code CLI. Usage: fake_claude.py MODE LOGFILE [cli args...].

Behaviour is controlled by MODE and FAKE_* environment variables. It never
touches real configuration: it only writes the file named by
CODEX_MONITOR_CLAUDE_CONFIG.
"""
import json
import os
import select
import subprocess
import sys
import time


def log(path, **entry):
    with open(path, 'a') as handle:
        handle.write(json.dumps(entry) + '\n')


def write_cache():
    target = os.environ['CODEX_MONITOR_CLAUDE_CONFIG']
    reset = os.environ['FAKE_RESET']
    account = os.environ.get('FAKE_ACCOUNT', 'synthetic-account-0001')
    doc = {
        'oauthAccount': {'accountUuid': 'synthetic-account-0001'},
        'cachedUsageUtilization': {
            'fetchedAtMs': int(os.environ['FAKE_FETCHED']) * 1000,
            'accountUuid': account,
            'utilization': {'five_hour': {'utilization': 21, 'resets_at': reset},
                            'seven_day': {'utilization': 44, 'resets_at': reset}},
        },
    }
    with open(target + '.tmp', 'w') as handle:
        json.dump(doc, handle)
    os.chmod(target + '.tmp', 0o600)
    os.replace(target + '.tmp', target)


def main():
    mode, logfile, args = sys.argv[1], sys.argv[2], sys.argv[3:]
    log(logfile, event='start', args=args, cwd=os.getcwd(), tty=os.isatty(0) and os.isatty(1), pid=os.getpid(),
        pgid=os.getpgid(0), sid=os.getsid(0))
    if mode == 'fail':
        sys.exit(2)
    if mode == 'noupdate':
        sys.exit(0)
    if mode in ('update', 'mismatch', 'expired'):
        write_cache()
    if mode == 'hang' or mode in ('update', 'mismatch', 'expired'):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        log(logfile, event='child', pid=child.pid)
        end = time.time() + 60
        while time.time() < end:
            if select.select([0], [], [], 0.1)[0]:
                data = os.read(0, 100)
                if data:
                    log(logfile, event='stdin', size=len(data))


if __name__ == '__main__':
    main()
