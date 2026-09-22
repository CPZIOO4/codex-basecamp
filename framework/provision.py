"""Explicit desktop role creation with durable uncertainty and per-role recovery."""
import json
import os
from pathlib import Path
import time

from locking import file_lock
from settings import project_config, state_root
from native_desktop import Desktop
from mailroom import atomic_json


def connect():
    return Desktop(os.environ.get('CODEX_THREAD_ID',''))


def role_prompt(root, spec):
    root=Path(root).resolve()
    return (f"你是本项目长期协作岗位“{spec['address']}”。所有岗位使用用户指定的同一项目目录：{root}。"
            f"先读根AGENTS.md、.agents/basecamp/WORKFLOW.md和.agents/skills/{spec['skill']}/SKILL.md。"
            "本次仅初始化岗位，确认职责后结束本轮，等待主控任务；不恢复历史工作、不发业务请求、不自行创建其他岗位。"
            "初始化过程尚在登记所有地址，此时不要发送交接消息。不要把自身期望模型、权限当作已验证设置。")


def provision(root, project_id=None, apply=False, shared_workspace=False, client=None):
    root=Path(root).resolve();config=project_config(root);state=state_root(root)
    roster_path=state/'roles.json';journal_path=state/'provision.json'
    if not apply:
        roster=json.loads(roster_path.read_text(encoding='utf-8')) if roster_path.exists() else {}
        return {'state':'preview','roles':[r['address'] for r in config['roles'] if r['address'] not in roster],
                'threads_created':0,'requires':'User request to create new roles sharing this project directory'}
    if not project_id or not shared_workspace:
        raise ValueError('Live provisioning requires --project-id and --shared-workspace, reflecting the user request')
    owned=client is None;client=client or connect()
    try:
        # Validate an actual project id returned by this installation; never guess one.
        projects=client.call('list_projects',{})
        def objects(value):
            if isinstance(value,dict):
                yield value
                for v in value.values():yield from objects(v)
            elif isinstance(value,list):
                for v in value:yield from objects(v)
        selected=next((p for p in objects(projects) if p.get('id',p.get('projectId'))==project_id),None)
        if not selected:raise ValueError('Project id not returned by list_projects')
        # A user must register this target folder in Codex; compare the published root.
        paths=[selected.get(k) for k in ('path','cwd','rootPath','directory','workingDirectory')]
        if not any(isinstance(p,str) and Path(p).resolve()==root for p in paths):
            raise ValueError('Project root cannot be matched to target directory; inspect list_projects and bind the correct project')
        with file_lock(state/'locks','provision'):
            roster=json.loads(roster_path.read_text(encoding='utf-8')) if roster_path.exists() else {}
            journal=json.loads(journal_path.read_text(encoding='utf-8')) if journal_path.exists() else {}
            created=[]
            for spec in config['roles']:
                address=spec['address']
                if address in roster:continue
                prior=journal.get(address,{})
                if prior.get('state') in ('creating','unknown','pending'):
                    return {'state':'needs_attention','role':address,'record':prior,'created':created,
                            'reason':'Creation may have succeeded. Bind the verified thread; do not create again.'}
                journal[address]={'state':'creating','started_at':time.time(),'project_id':project_id}
                atomic_json(journal_path,journal)
                request={'title':config['name']+' · '+address,'prompt':role_prompt(root,spec),
                         'target':{'type':'project','projectId':project_id,'environment':{'type':'local'}}}
                if config.get('model_policy')=='explicit':
                    if spec.get('model'):request['model']=spec['model']
                    if spec.get('thinking'):request['thinking']=spec['thinking']
                try:
                    answer=client.call('create_thread',request)
                    tid=answer.get('threadId');host=answer.get('hostId','local')
                    if not tid:
                        journal[address]={'state':'pending','response':answer};atomic_json(journal_path,journal)
                        return {'state':'needs_attention','role':address,'record':journal[address],'created':created}
                    if host!='local':raise RuntimeError('This release supports local desktop projects only')
                    requested={k:request[k] for k in ('model','thinking') if k in request} or 'inherit'
                    roster[address]={**spec,'thread_id':tid,'host_id':host,'settings':{'requested':requested,'observed':None}}
                    atomic_json(roster_path,roster)
                    journal[address]={'state':'registered','thread_id':tid};atomic_json(journal_path,journal)
                    created.append(address)
                except Exception as exc:
                    journal[address]={'state':'unknown','error':str(exc),'request_title':request['title']}
                    atomic_json(journal_path,journal)
                    return {'state':'needs_attention','role':address,'created':created,'reason':'Creation outcome unknown; inspect and bind before retrying'}
            return {'state':'roles_registered','registered':len(roster),'created':created,
                    'settings_verified':False,'readiness':'Creation registered; use status to observe initialization completion'}
    finally:
        if owned:client.close()


def bind(root,address,thread_id,client=None):
    root=Path(root).resolve();config=project_config(root);state=state_root(root)
    spec=next((r for r in config['roles'] if r['address']==address),None)
    if spec is None:raise ValueError('Unknown role address')
    # wait_threads deliberately omits cwd and cannot inspect the calling thread.
    # Use the read-only native metadata endpoint to verify project ownership.
    from mailroom import Native,configure
    configure(root)
    owned=client is None;client=client or Native()
    try:
        answer=client.call('thread/read',{'threadId':thread_id,'includeTurns':False})
        thread=answer.get('thread',{})
        if thread.get('id')!=thread_id:raise ValueError('Thread not verified')
        if not thread.get('cwd') or Path(thread['cwd']).resolve()!=root:raise ValueError('Thread belongs to a different project directory')
        section=thread.get('section')
        if thread.get('archived') or section=='archived' or (isinstance(section,dict) and section.get('type')=='archived'):
            raise ValueError('Do not bind an archived role')
        with file_lock(state/'locks','provision'):
            path=state/'roles.json';roster=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            if address in roster and roster[address]['thread_id']!=thread_id:raise ValueError('Role already bound to another thread')
            if any(r['thread_id']==thread_id and a!=address for a,r in roster.items()):raise ValueError('Thread already bound to another role')
            observed={k:thread[k] for k in ('model','reasoningEffort') if thread.get(k) is not None}
            roster[address]={**spec,'thread_id':thread_id,'host_id':'local','settings':{'requested':'inherit','observed':observed or None}}
            atomic_json(path,roster)
        return {'state':'registered','role':address,'thread_id':thread_id}
    finally:
        if owned:client.close()
