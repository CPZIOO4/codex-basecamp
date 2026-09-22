"""Native queue adapter with desktop-owned activation; never owns execution."""
import argparse
import errno
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from locking import file_lock
if os.name == "nt":
    from locking import msvcrt
from collections import deque

from settings import local_config, resolve_cli, state_root
ROOT = Path.cwd() / '.codex-basecamp'
CONFIG = {}
ROSTER = {}
ROLES = {}

def configure(project_root):
    global ROOT, CONFIG, ROSTER, ROLES
    ROOT = state_root(project_root)
    CONFIG = local_config(project_root)
    roster_path = ROOT / 'roles.json'
    ROSTER = json.loads(roster_path.read_text(encoding='utf-8')) if roster_path.exists() else {}
    ROLES = {address: item['thread_id'] for address, item in ROSTER.items()}


class Native:
    def __init__(self):
        config = CONFIG
        try:
            self.p = subprocess.Popen(
                [*resolve_cli(config), 'app-server', '--stdio'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as exc:
            raise RuntimeError('phase=process_start; ' + str(exc)) from exc
        self.stderr_tail = deque(maxlen=16)
        self.stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self.stderr_reader.start()
        self.responses = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.seq = 0
        try:
            self.call('initialize', {'clientInfo': {'name': 'codex-basecamp', 'version': '0.1'},
                                    'capabilities': {'experimentalApi': True}})
        except Exception as exc:
            try:
                self.p.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
            code = self.p.poll()
            self.stderr_reader.join(timeout=0.2)
            detail = ''.join(self.stderr_tail)[-4000:]
            try:
                self.close()
            except Exception:
                pass
            raise RuntimeError('phase=initialize; exit_code=' + str(code) + '; ' + str(exc) + '; stderr=' + detail) from exc
        self._write({'method': 'initialized', 'params': {}})

    def _write(self, data):
        self.p.stdin.write((json.dumps(data, ensure_ascii=False) + '\n').encode('utf-8'))
        self.p.stdin.flush()

    def _read_stderr(self):
        for line in self.p.stderr:
            self.stderr_tail.append(line.decode('utf-8', errors='replace'))

    def _read(self):
        for line in self.p.stdout:
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if 'id' in data and 'method' not in data:
                self.responses.put(data)
        self.responses.put({'error': {'message': 'connection closed'}})

    def call(self, method, params):
        self.seq += 1
        self._write({'id': self.seq, 'method': method, 'params': params})
        result = self.responses.get(timeout=20)
        if 'error' in result:
            raise RuntimeError(str(result['error']))
        if result.get('id') != self.seq:
            raise RuntimeError('unexpected reply; delivery unknown')
        return result['result']

    def close(self):
        self.p.stdin.close()
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.p.terminate()  # Only this short-lived transport process, never a desktop task.
            self.p.wait(timeout=5)
        self.reader.join(timeout=1)
        self.stderr_reader.join(timeout=1)
        self.p.stdout.close()
        self.p.stderr.close()


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.' + str(uuid.uuid4()) + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


@contextmanager
def recipient_lock(tid, timeout=120):
    with file_lock(ROOT / 'transport-state', tid, timeout) as path:
        yield path


def send(recipient, text, return_to=None):
    # Serialise enqueue calls only; the native queue owns task scheduling.
    if recipient not in ROLES:
        raise ValueError('Recipient is not registered')
    if ROSTER[recipient].get('host_id', 'local') != 'local':
        raise ValueError('Native queue adapter supports local desktop roles only')
    with recipient_lock(ROLES[recipient]) as state_path:
        previous = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
        if previous.get('state') in ('sending', 'unknown'):
            return {'recipient': recipient, 'state': 'needs_attention',
                    'blocked_by': previous.get('receipt'),
                    'reason': 'Previous delivery is unresolved; no message sent.'}
        result = _send(recipient, text, state_path, return_to)
    if result['state'] == 'accepted':
        result['activation'] = activate_receipt(result['receipt'])
    return result


def desktop_client():
    from native_desktop import Desktop
    return Desktop(os.environ['CODEX_THREAD_ID'])

def snapshot(client, tid, timeout=5):
    result = client.call('wait_threads', {'targets': [{'threadId': tid, 'hostId': 'local'}],
                                         'timeoutMs': 0}, timeout=timeout)
    polls = result.get('polls', [])
    if not polls or polls[0].get('thread', {}).get('id') != tid:
        raise RuntimeError('Desktop status unavailable')
    return polls[0]


def activate_receipt(receipt_path):
    path = Path(receipt_path).resolve()
    if path.parent != (ROOT / 'receipts').resolve():
        raise ValueError('Use a receipt from this mailroom')
    row = json.loads(path.read_text(encoding='utf-8'))
    if row.get('state') != 'accepted':
        raise ValueError('Activation requires an accepted message; no message will be sent')
    activation = {'state': 'checking', 'checked_at': time.time()}
    client = None
    try:
        if row['thread_id'] == os.environ.get('CODEX_THREAD_ID'):
            activation['state'] = 'not_needed'
            activation['reason'] = 'Calling thread is already active; queued work is not yet complete'
            return activation
        client = desktop_client()
        first = snapshot(client, row['thread_id'])
        activation['initial_status'] = first['thread']['status']['type']
        if activation['initial_status'] != 'notLoaded':
            if activation['initial_status'] not in ('idle', 'active'):
                raise RuntimeError('Desktop target status: ' + activation['initial_status'])
            activation['state'] = 'not_needed'
        else:
            # The shared page lock covers navigation only; enqueue locks are released.
            with recipient_lock('_desktop_page'):
                current = snapshot(client, row['thread_id'])
                if current['thread']['status']['type'] != 'notLoaded':
                    if current['thread']['status']['type'] not in ('idle', 'active'):
                        raise RuntimeError('Desktop target is not available for activation')
                    activation['state'] = 'not_needed'
                else:
                    previous_turn = (current.get('latestTurn') or {}).get('id')
                    try:
                        client.call('navigate_to_codex_page', {'threadId': row['thread_id']}, timeout=10)
                        activation['opened_at'] = time.time()
                        deadline = time.monotonic() + 30
                        activation['state'] = 'start_unconfirmed'
                        while time.monotonic() < deadline:
                            current = snapshot(client, row['thread_id'], timeout=max(0.1, min(5, deadline-time.monotonic())))
                            turn = current.get('latestTurn') or {}
                            if turn.get('id') and turn['id'] != previous_turn:
                                activation.update(state='started', turn_id=turn['id'],
                                                  started_at=turn.get('startedAt'), observed_at=time.time())
                                break
                            time.sleep(min(1, max(0, deadline-time.monotonic())))
                    finally:
                        try:
                            client.call('navigate_to_codex_page', {'threadId': row['return_thread_id']}, timeout=10)
                            activation['returned_at'] = time.time()
                        except Exception as exc:
                            activation['return_error'] = str(exc)
    except Exception as exc:
        activation.update(state='failed', error=str(exc))
    finally:
        if client:
            try:
                client.close()
            except Exception as exc:
                activation['connection_close_error'] = str(exc)
        activation['finished_at'] = time.time()
        row['activation'] = activation
        atomic_json(path, row)
    return activation


def _send(recipient, text, state_path, return_to=None):
    tid = ROLES[recipient]
    ident = str(uuid.uuid4())
    receipt = ROOT / 'receipts' / (ident + '.json')
    receipt.parent.mkdir(parents=True, exist_ok=True)
    sender = os.environ.get('CODEX_THREAD_ID')
    sender_role = next((r for r in ROSTER.values() if r['thread_id'] == sender), None)
    if sender_role is None:
        raise ValueError('Sender is not a registered project role')
    domain = sender_role['domain']
    return_address = return_to or (domain + ' / 主控')
    if return_address not in ROLES or not return_address.endswith(' / 主控'):
        raise ValueError('Return target must be a registered controller')
    row = {'return_thread_id': ROLES[return_address], 'message_id': ident, 'sender': os.environ.get('CODEX_THREAD_ID'),
           'recipient': recipient, 'thread_id': tid, 'text': text,
           'created_at': time.time(), 'state': 'preparing'}
    def save():
        atomic_json(receipt, row)
    save()
    client = None
    try:
        client = Native()
        row.update(state='sending', sent_at=time.time())
        save()
        atomic_json(state_path, {'state': 'sending', 'receipt': str(receipt)})
        answer = client.call('thread/queue/add', {'threadId': tid,
            'clientUserMessageId': ident, 'input': [{'type': 'text', 'text': text}]})
        row.update(state='accepted', accepted_at=time.time(), response=answer)
    except Exception as exc:
        row.update(state='unknown' if row['state'] == 'sending' else 'not_sent',
                   error=str(exc))
    finally:
        save()
        atomic_json(state_path, {'state': row['state'], 'receipt': str(receipt)})
        if client:
            try:
                client.close()
            except Exception as exc:
                row['transport_close_error'] = str(exc)
                save()
    return {k: row.get(k) for k in ['message_id', 'recipient', 'state', 'created_at', 'accepted_at']} | {'receipt': str(receipt)}
