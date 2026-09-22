import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'framework'))
from scaffold import install
from provision import provision,bind


class DesktopFixture:
    def __init__(self,root):self.root=root;self.created=[];self.fail_at=None;self.pending=False
    def call(self,name,params):
        if name=='list_projects':return {'projects':[{'projectId':'fixture-project','path':str(self.root),'isGitRepository':True,'hostId':'local'}]}
        if name=='create_thread':
            self.created.append(params)
            if self.fail_at==len(self.created):raise TimeoutError('Creation response lost')
            if self.pending:return {'clientThreadId':'pending-fixture'}
            return {'threadId':'fixture-role-'+str(len(self.created)),'hostId':'local'}
        if name=='thread/read':return {'thread':{'id':params['threadId'],'cwd':str(self.root),'status':{'type':'idle'}}}
        raise AssertionError(name)


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'project';install(self.root,layout='compact')
        self.desktop=DesktopFixture(self.root)
    def tearDown(self):self.temp.cleanup()
    def run_provision(self):return provision(self.root,'fixture-project',True,True,self.desktop)

    def test_preview_never_creates_threads(self):
        result=provision(self.root,client=self.desktop)
        self.assertEqual(len(result['roles']),5);self.assertEqual(self.desktop.created,[])

    def test_complete_provision_and_repeat_create_exactly_once(self):
        self.assertEqual(self.run_provision()['state'],'roles_registered')
        self.assertEqual(self.run_provision()['created'],[])
        self.assertEqual(len(self.desktop.created),5)
        self.assertNotIn('model',self.desktop.created[0]);self.assertNotIn('thinking',self.desktop.created[0])
        self.assertEqual(self.desktop.created[0]['target']['environment'],{'type':'local'})

    def test_shared_workspace_and_project_binding_are_explicit(self):
        with self.assertRaises(ValueError):provision(self.root,'fixture-project',True,False,self.desktop)
        with self.assertRaises(ValueError):provision(self.root,'not-returned',True,True,self.desktop)
        self.desktop.root=self.root.parent/'other'
        with self.assertRaisesRegex(ValueError,'root'):self.run_provision()
        self.assertEqual(self.desktop.created,[])

    def test_creation_timeout_survives_restart_without_duplicate(self):
        self.desktop.fail_at=2
        self.assertEqual(self.run_provision()['state'],'needs_attention')
        self.desktop.fail_at=None
        self.assertEqual(self.run_provision()['state'],'needs_attention')
        self.assertEqual(len(self.desktop.created),2)
        roster=json.loads((self.root/'.codex-basecamp/roles.json').read_text())
        self.assertEqual(len(roster),1)
        bind(self.root,'Main / 规划场1','verified-existing-role',self.desktop)
        self.assertEqual(self.run_provision()['state'],'roles_registered')
        self.assertEqual(len(self.desktop.created),5)

    def test_pending_client_id_is_not_registered_as_real_thread(self):
        self.desktop.pending=True;self.run_provision();self.run_provision()
        self.assertEqual(len(self.desktop.created),1)
        self.assertFalse((self.root/'.codex-basecamp/roles.json').exists())

    def test_binding_cannot_reuse_one_thread_for_two_roles(self):
        bind(self.root,'Main / 主控','fixture-thread',self.desktop)
        with self.assertRaisesRegex(ValueError,'another role'):bind(self.root,'Main / 规划场1','fixture-thread',self.desktop)

    def test_binding_another_project_is_rejected(self):
        self.desktop.root=self.root.parent
        with self.assertRaisesRegex(ValueError,'different project'):bind(self.root,'Main / 主控','fixture-thread',self.desktop)

    def test_explicit_model_preferences_only_apply_when_policy_selected(self):
        path=self.root/'.agents/basecamp/project.json';config=json.loads(path.read_text(encoding='utf-8'))
        config['model_policy']='explicit';config['roles'][0].update(model='user-selected-model',thinking='high')
        path.write_text(json.dumps(config),encoding='utf-8')
        self.run_provision()
        self.assertEqual(self.desktop.created[0]['model'],'user-selected-model')
        self.assertNotIn('model',self.desktop.created[1])
        roster=json.loads((self.root/'.codex-basecamp/roles.json').read_text(encoding='utf-8'))
        self.assertEqual(roster['Main / 主控']['settings']['requested']['thinking'],'high')
        self.assertIsNone(roster['Main / 主控']['settings']['observed'])
