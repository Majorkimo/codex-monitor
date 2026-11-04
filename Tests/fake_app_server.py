"""Fake `codex app-server --stdio` for tests. Synthetic data only; no auth, no network.

Usage: fake_app_server.py MODE LOG_PATH [app-server --stdio]

Every launch and every received message is appended to LOG_PATH as JSON lines.
MODE selects a behavior (see below). Counters for modes that span restarts live
in files next to LOG_PATH.
"""
import json
import os
import sys
import time
from pathlib import Path

mode = sys.argv[1]
log_path = Path(sys.argv[2])

RATE_LIMITS = {
    'rateLimitsByLimitId': {'codex': {
        'primary': {'usedPercent': 41, 'windowDurationMins': 300, 'resetsAt': 1893456000},
        'secondary': {'usedPercent': 17.6, 'windowDurationMins': 10080, 'resetsAt': 1893801600},
        'credits': {'hasCredits': True, 'unlimited': False, 'balance': '1234.56789'},
    }},
    'rateLimitResetCredits': {
        'availableCount': 2,
        'credits': [{'status': 'available', 'expiresAt': 1894000000}],
    },
}
CREDITS_ONLY = {'rateLimitsByLimitId': {'codex': {
    'credits': {'hasCredits': True, 'unlimited': False, 'balance': '7.25'},
}}}


def log(entry):
    with log_path.open('a') as handle:
        handle.write(json.dumps(entry) + '\n')


def write(data):
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def send(message, split=False):
    data = json.dumps(message).encode() + b'\n'
    if split:  # a reply that arrives in two chunks
        write(data[:7])
        time.sleep(0.05)
        write(data[7:])
    else:
        write(data)


def bump(name):
    """Persistent counter shared by successive launches; returns the old value."""
    path = log_path.with_name(log_path.name + '.' + name)
    count = int(path.read_text()) if path.exists() else 0
    path.write_text(str(count + 1))
    return count


log({'event': 'start', 'argv': sys.argv[3:], 'codex_home': os.environ.get('CODEX_HOME')})
launch = bump('launches')

if mode == 'exit_immediately':
    sys.exit(4)
if mode == 'partial_then_exit' and launch == 0:
    write(b'{"id":1,"res')  # truncated line, then die
    sys.exit(0)

for line in sys.stdin.buffer:
    message = json.loads(line)
    method = message.get('method')
    log({'event': 'message', 'method': method, 'params': message.get('params')})
    if 'id' not in message:
        continue
    request_id = message['id']

    if method == 'initialize':
        send({'method': 'serverNotice', 'params': {}})  # unsolicited notification
        write(b'this is not json\n')
        send({'id': request_id, 'result': {'userAgent': 'fake'}}, split=True)
        if mode == 'die_after_init':
            os._exit(3)
    elif method == 'account/read':
        send({'id': request_id, 'result': {'account': {
            'type': 'chatgpt', 'email': 'person@example.invalid', 'planType': 'plus'}}})
    elif method == 'account/rateLimits/read':
        calls = bump('limits')
        send({'id': 987654, 'result': {'stale': True}})  # reply to some other request
        if mode == 'hang':
            time.sleep(60)
        elif mode == 'error' or (mode == 'second_call_error' and calls == 1):
            send({'id': request_id, 'error': {'code': -32000, 'message': 'boom token=sk-SYNTHETIC-SECRET'}})
        elif mode == 'unauthorized':
            send({'id': request_id, 'error': {'code': -32001, 'message': '401 Unauthorized for person@example.invalid'}})
        elif mode == 'credits_only':
            send({'id': request_id, 'result': CREDITS_ONLY})
        elif mode == 'empty':
            send({'id': request_id, 'result': {}})
        else:
            send({'id': request_id, 'result': RATE_LIMITS})
    else:
        send({'id': request_id, 'error': {'code': -32601, 'message': 'unknown method'}})
