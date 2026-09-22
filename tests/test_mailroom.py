"""Transport failure semantics only. No desktop task or API call is made."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "framework"))
import mailroom

mailroom.ROSTER = {f"{d} / {r}": {"thread_id": f"fixture-{d}-{i}", "domain": d} for d in ("Alpha", "Beta") for i,r in enumerate(["主控", "分析场A", "试验场1", "试验场4"])}
mailroom.ROLES = {k:v["thread_id"] for k,v in mailroom.ROSTER.items()}


class FakeNative:
    calls = []
    fail_send = False
    fail_close = False

    def call(self, method, params):
        self.calls.append((method, params))
        if self.fail_send:
            raise TimeoutError('reply missing after write')
        return {'accepted': True}

    def close(self):
        if self.fail_close:
            raise RuntimeError('cleanup failed')


class FakeDesktop:
    calls = []
    cold = False
    fail_open = False
    opened = False

    def call(self, name, params, timeout=5):
        self.calls.append((name, params))
        if name == 'wait_threads':
            tid = params['targets'][0]['threadId']
            return {'polls': [{'thread': {'id': tid, 'status': {'type': 'notLoaded' if self.cold and not self.opened else 'active'}},
                               'latestTurn': {'id': 'new' if self.opened else 'old', 'startedAt': 123}}]}
        if name == 'navigate_to_codex_page':
            if params['threadId'] != mailroom.ROLES['Alpha / 主控']:
                if self.fail_open:
                    raise RuntimeError('desktop opening failed')
                self.opened = True
            return {'navigated': True}

    def close(self):
        pass


class TransportTests(unittest.TestCase):
    def setUp(self):
        roster={f"{d} / {r}": {"thread_id": f"fixture-{d}-{i}", "domain": d} for d in ("Alpha", "Beta") for i,r in enumerate(["主控", "分析场A", "试验场1", "试验场4"])}
        self.roster_patch=patch.object(mailroom,'ROSTER',roster)
        self.roles_patch=patch.object(mailroom,'ROLES',{k:v['thread_id'] for k,v in roster.items()})
        self.roster_patch.start();self.roles_patch.start()
        self.identity_patch = patch.dict(mailroom.os.environ, {'CODEX_THREAD_ID': mailroom.ROLES['Alpha / 主控']})
        self.identity_patch.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.root_patch = patch.object(mailroom, 'ROOT', Path(self.tmp.name))
        self.root_patch.start()
        FakeNative.calls = []
        FakeNative.fail_send = False
        FakeNative.fail_close = False
        self.native_patch = patch.object(mailroom, 'Native', FakeNative)
        self.native_patch.start()
        FakeDesktop.calls = []
        FakeDesktop.cold = FakeDesktop.fail_open = FakeDesktop.opened = False
        self.desktop_patch = patch.object(mailroom, 'desktop_client', FakeDesktop)
        self.desktop_patch.start()

    def test_return_uses_sender_domain_across_domain_message(self):
        with patch.dict(mailroom.os.environ, {'CODEX_THREAD_ID': mailroom.ROLES['Beta / 试验场4']}):
            result = mailroom.send('Alpha / 分析场A', 'cross domain')
        row = json.loads(Path(result['receipt']).read_text(encoding='utf-8'))
        self.assertEqual(row['return_thread_id'], mailroom.ROLES['Beta / 主控'])

    def test_validation_can_return_to_other_domain_controller(self):
        with patch.dict(mailroom.os.environ, {'CODEX_THREAD_ID': mailroom.ROLES['Beta / 试验场4']}):
            result = mailroom.send('Beta / 分析场A', 'validation', 'Alpha / 主控')
        row = json.loads(Path(result['receipt']).read_text(encoding='utf-8'))
        self.assertEqual(row['return_thread_id'], mailroom.ROLES['Alpha / 主控'])

    def test_invalid_return_does_not_send(self):
        with self.assertRaises(ValueError):
            mailroom.send('Alpha / 分析场A', 'invalid', 'Alpha / 试验场1')
        self.assertEqual(FakeNative.calls, [])

    def tearDown(self):
        self.identity_patch.stop()
        self.native_patch.stop()
        self.desktop_patch.stop()
        self.root_patch.stop()
        self.tmp.cleanup()
        self.roster_patch.stop();self.roles_patch.stop()

    @unittest.skipUnless(mailroom.os.name == "nt", "Windows lock behavior")
    def test_windows_lock_contention_retries_then_releases(self):
        with patch.object(mailroom.msvcrt, 'locking', side_effect=[OSError(36, 'busy'), None, None]) as lock:
            with mailroom.recipient_lock('_desktop_page'):
                pass
        self.assertEqual(lock.call_count, 3)
        self.assertEqual(lock.call_args.args[1], mailroom.msvcrt.LK_UNLCK)

    @unittest.skipUnless(mailroom.os.name == "nt", "Windows lock behavior")
    def test_lock_timeout_does_not_unlock_unowned_lock(self):
        with patch.object(mailroom.msvcrt, 'locking', side_effect=OSError(36, 'busy')) as lock:
            with self.assertRaises(TimeoutError):
                with mailroom.recipient_lock('_desktop_page', timeout=0):
                    self.fail('must not acquire')
        self.assertEqual(lock.call_count, 1)

    def test_unknown_blocks_same_recipient_but_not_other_domain(self):
        FakeNative.fail_send = True
        first = mailroom.send('Alpha / 分析场A', 'one')
        self.assertEqual(first['state'], 'unknown')
        FakeNative.fail_send = False
        self.assertEqual(mailroom.send('Alpha / 分析场A', 'two')['state'], 'needs_attention')
        self.assertEqual(mailroom.send('Beta / 分析场A', 'other')['state'], 'accepted')
        self.assertEqual(len(FakeNative.calls), 2)

    def test_initialization_failure_is_not_sent_and_does_not_block(self):
        with patch.object(mailroom, 'Native', side_effect=OSError('cannot start')):
            self.assertEqual(mailroom.send('Alpha / 分析场A', 'one')['state'], 'not_sent')
        self.assertEqual(mailroom.send('Alpha / 分析场A', 'two')['state'], 'accepted')
        self.assertEqual(len(FakeNative.calls), 1)

    def test_close_error_does_not_reclassify_accepted_or_resend(self):
        FakeNative.fail_close = True
        result = mailroom.send('Alpha / 分析场A', 'one')
        self.assertEqual(result['state'], 'accepted')
        self.assertEqual(len(FakeNative.calls), 1)
        receipt = json.loads(Path(result['receipt']).read_text(encoding='utf-8'))
        self.assertIn('transport_close_error', receipt)

    def test_persisted_inflight_blocks_after_process_restart(self):
        directory = mailroom.ROOT / 'transport-state'
        directory.mkdir()
        tid = mailroom.ROLES['Alpha / 分析场A']
        mailroom.atomic_json(directory / (tid + '.json'), {'state': 'sending', 'receipt': 'old.json'})
        result = mailroom.send('Alpha / 分析场A', 'new')
        self.assertEqual(result['state'], 'needs_attention')
        self.assertEqual(FakeNative.calls, [])

    def test_cold_recipient_opens_then_returns_without_direct_start(self):
        FakeDesktop.cold = True
        result = mailroom.send('Alpha / 分析场A', 'one')
        self.assertEqual(result['activation']['state'], 'started')
        navigation = [p['threadId'] for n, p in FakeDesktop.calls if n == 'navigate_to_codex_page']
        self.assertEqual(navigation, [mailroom.ROLES['Alpha / 分析场A'], mailroom.ROLES['Alpha / 主控']])
        self.assertEqual([n for n, p in FakeNative.calls], ['thread/queue/add'])

    def test_busy_recipient_does_not_navigate_or_interrupt(self):
        result = mailroom.send('Alpha / 分析场A', 'one')
        self.assertEqual(result['activation']['state'], 'not_needed')
        self.assertEqual([n for n, p in FakeDesktop.calls], ['wait_threads'])

    def test_cold_recipient_returns_to_own_domain_controller(self):
        FakeDesktop.cold = True
        with patch.dict(mailroom.os.environ, {'CODEX_THREAD_ID': mailroom.ROLES['Beta / 试验场4']}):
            result = mailroom.send('Beta / 分析场A', 'one')
        self.assertEqual(result['activation']['state'], 'started')
        navigation = [p['threadId'] for n, p in FakeDesktop.calls if n == 'navigate_to_codex_page']
        self.assertEqual(navigation, [mailroom.ROLES['Beta / 分析场A'], mailroom.ROLES['Beta / 主控']])
        self.assertEqual([n for n, p in FakeNative.calls], ['thread/queue/add'])

    def test_activation_failure_preserves_accepted_and_retry_never_resends(self):
        FakeDesktop.cold = FakeDesktop.fail_open = True
        result = mailroom.send('Alpha / 分析场A', 'one')
        self.assertEqual(result['state'], 'accepted')
        self.assertEqual(result['activation']['state'], 'failed')
        FakeDesktop.fail_open = False
        retried = mailroom.activate_receipt(result['receipt'])
        self.assertEqual(retried['state'], 'started')
        self.assertEqual(len(FakeNative.calls), 1)
        receipt = json.loads(Path(result['receipt']).read_text(encoding='utf-8'))
        self.assertEqual(receipt['state'], 'accepted')


if __name__ == '__main__':
    unittest.main()
