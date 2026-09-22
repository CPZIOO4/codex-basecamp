import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'framework'))
from scaffold import install


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'项目 with spaces'
    def tearDown(self):self.temp.cleanup()

    def test_clean_install_is_idempotent_and_has_no_thread_ids(self):
        first=install(self.root);second=install(self.root)
        self.assertEqual(first['roles_planned'],13)
        self.assertEqual(second['changed_files'],[])
        self.assertEqual(first['threads_created'],0)
        self.assertFalse((self.root/'.codex-basecamp/roles.json').exists())
        config=json.loads((self.root/'.agents/basecamp/project.json').read_text(encoding='utf-8'))
        self.assertNotIn('thread_id',json.dumps(config))

    def test_existing_project_readme_and_instructions_are_preserved(self):
        self.root.mkdir();(self.root/'README.md').write_bytes(b'Existing business README\r\n')
        (self.root/'AGENTS.md').write_text('User rules\n',encoding='utf-8')
        (self.root/'app.py').write_bytes(b'print("business")\n')
        install(self.root,domains=['产品','数据'],layout='compact')
        self.assertEqual((self.root/'README.md').read_bytes(),b'Existing business README\r\n')
        self.assertTrue((self.root/'AGENTS.md').read_text(encoding='utf-8').startswith('User rules'))
        self.assertEqual((self.root/'app.py').read_bytes(),b'print("business")\n')
        self.assertEqual(install(self.root)['changed_files'],[])

    def test_conflict_is_detected_before_writing_other_files(self):
        path=self.root/'.agents/basecamp/mailroom.py';path.parent.mkdir(parents=True);path.write_text('user change')
        with self.assertRaisesRegex(ValueError,'nothing installed'):install(self.root)
        self.assertEqual(path.read_text(),'user change')
        self.assertFalse((self.root/'AGENTS.md').exists())

    def test_dry_run_does_not_create_target(self):
        result=install(self.root,dry_run=True)
        self.assertEqual(result['state'],'preview');self.assertFalse(self.root.exists())

    def test_installed_kit_runs_and_expands_another_project_without_source_imports(self):
        install(self.root,layout='compact')
        other=Path(self.temp.name)/'next project'
        command=[sys.executable,str(self.root/'.agents/basecamp/run.py'),'init','--target',str(other)]
        result=subprocess.run(command,cwd=self.temp.name,capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['state'],'files_ready')
        self.assertTrue((other/'.agents/skills/camp-plan/SKILL.md').is_file())

    def test_changed_managed_instructions_are_not_silently_replaced(self):
        install(self.root);path=self.root/'AGENTS.md'
        text=path.read_text(encoding='utf-8').replace('默认0','默认2');path.write_text(text,encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'edited'):install(self.root)
        self.assertIn('默认2',path.read_text(encoding='utf-8'))

    def test_invalid_domain_cannot_escape_paths(self):
        with self.assertRaises(ValueError):install(self.root,domains=['../../escape'])

    def test_linked_installation_subdirectory_is_rejected(self):
        self.root.mkdir();outside=Path(self.temp.name)/'outside';outside.mkdir()
        try:(self.root/'.agents').symlink_to(outside,target_is_directory=True)
        except OSError:self.skipTest('Symlink creation unavailable')
        with self.assertRaisesRegex(ValueError,'Linked'):install(self.root)
        self.assertEqual(list(outside.iterdir()),[])
