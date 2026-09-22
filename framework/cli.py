"""Single entry point for expansion, capability checks, roles and queued handoffs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from scaffold import install
from settings import project_config, local_config, resolve_cli, state_root


def doctor(root, live=False):
    root=Path(root).resolve();config=project_config(root);checks=[]
    checks.append({'check':'project','state':'pass','roles_planned':len(config['roles'])})
    try:
        command=resolve_cli(local_config(root))
        answer=subprocess.run([*command,'--version'],capture_output=True,text=True,timeout=15,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if answer.returncode:raise RuntimeError('Codex CLI version command failed')
        checks.append({'check':'codex_cli','state':'pass','version':answer.stdout.strip()})
    except Exception as exc:checks.append({'check':'codex_cli','state':'unavailable','reason':str(exc)})
    if live:
        from provision import connect
        from mailroom import Native,configure
        client=None;native=None
        try:
            client=connect()
            required={'create_thread','list_projects','wait_threads','navigate_to_codex_page'}
            missing=required-client.names
            if missing:raise RuntimeError('Missing desktop tools: '+', '.join(sorted(missing)))
            checks.append({'check':'desktop_tools','state':'pass','tools':sorted(required)})
        except Exception as exc:checks.append({'check':'desktop_tools','state':'unavailable','reason':str(exc)})
        finally:
            if client:client.close()
        try:
            configure(root);native=Native()
            # Local schema generation is read-only and avoids probing a queue with a message.
            import tempfile
            with tempfile.TemporaryDirectory(prefix='basecamp-schema-') as directory:
                result=subprocess.run([*resolve_cli(local_config(root)),'app-server','generate-json-schema','--experimental','--out',directory],
                    capture_output=True,text=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if result.returncode:raise RuntimeError('Experimental schema generation unavailable')
                found=any('thread/queue/add' in p.read_text(encoding='utf-8') for p in Path(directory).rglob('*.json'))
                if not found:raise RuntimeError('thread/queue/add not advertised by this CLI')
            checks.append({'check':'native_queue_schema','state':'pass','method':'thread/queue/add'})
        except Exception as exc:checks.append({'check':'native_queue_schema','state':'unavailable','reason':str(exc)})
        finally:
            if native:native.close()
    else:
        checks.append({'check':'live_desktop_and_queue','state':'not_checked','reason':'Run doctor --live inside the Codex desktop task'})
    ready=all(c['state']=='pass' for c in checks)
    return {'state':'capabilities_ready' if ready else 'attention_required','checks':checks,
            'business_calls':0,'threads_created':0,'messages_sent':0,
            'limit':'Capability checks do not prove a complete live role handoff or model/permission settings'}


def status(root, live=False):
    path=state_root(root)/'roles.json';roster=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    result={'state':'registered' if roster else 'not_deployed','roles':roster,'live_checked':False}
    if live and roster:
        from provision import connect
        client=connect();snapshots=[]
        try:
            targets=[{'threadId':r['thread_id'],'hostId':r.get('host_id','local')} for r in roster.values()
                     if r['thread_id']!=os.environ.get('CODEX_THREAD_ID')]
            for i in range(0,len(targets),8):
                snapshots.append(client.call('wait_threads',{'targets':targets[i:i+8],'timeoutMs':0}))
        finally:client.close()
        result.update(live_checked=True,snapshots=snapshots,
                      calling_thread_excluded='The active caller cannot wait on itself')
    return result


def main(argv=None):
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description='Codex Basecamp: expand an independent multi-conversation project')
    sub=parser.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init');init.add_argument('--target',required=True);init.add_argument('--name')
    init.add_argument('--domain',action='append');init.add_argument('--layout',choices=['research','compact']);init.add_argument('--dry-run',action='store_true')
    for name in ('doctor','status','projects','provision','bind','send','wake'):
        p=sub.add_parser(name);p.add_argument('--root',default='.')
        if name in ('doctor','status'):p.add_argument('--live',action='store_true')
        elif name=='provision':
            p.add_argument('--project-id');p.add_argument('--apply',action='store_true');p.add_argument('--shared-workspace',action='store_true')
        elif name=='bind':p.add_argument('--role',required=True);p.add_argument('--thread-id',required=True)
        elif name=='send':p.add_argument('--to',required=True);p.add_argument('--file',required=True);p.add_argument('--return-to')
        elif name=='wake':p.add_argument('--receipt',required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='init':result=install(args.target,args.name,args.domain,args.layout,args.dry_run)
        elif args.command=='doctor':result=doctor(args.root,args.live)
        elif args.command=='status':result=status(args.root,args.live)
        elif args.command=='projects':
            from provision import connect
            client=connect()
            try:result=client.call('list_projects',{})
            finally:client.close()
        elif args.command=='provision':
            from provision import provision
            result=provision(args.root,args.project_id,args.apply,args.shared_workspace)
        elif args.command=='bind':
            from provision import bind
            result=bind(args.root,args.role,args.thread_id)
        else:
            import mailroom
            project_config(args.root);mailroom.configure(args.root)
            if args.command=='send':result=mailroom.send(args.to,Path(args.file).read_text(encoding='utf-8-sig'),args.return_to)
            else:result=mailroom.activate_receipt(args.receipt)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 2 if result.get('state') in ('attention_required','needs_attention','unknown','not_sent','failed','start_unconfirmed') else 0
    except Exception as exc:
        print(json.dumps({'state':'error','error':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2
