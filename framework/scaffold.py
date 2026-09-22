"""Expand the kit without overwriting an existing project's files."""
from pathlib import Path
import hashlib
import json
import re

VERSION = '1.0.0'
START = '<!-- codex-basecamp:start -->'
END = '<!-- codex-basecamp:end -->'


def kit_paths():
    code = Path(__file__).resolve().parent
    templates = code / 'templates' if (code / 'templates').is_dir() else code.parent / 'templates'
    launcher = code / 'run.py' if (code / 'run.py').is_file() else code.parent / 'basecamp.py'
    return code, templates, launcher


def role_specs(domains, layout):
    if layout not in ('research', 'compact'):
        raise ValueError('Unknown layout')
    definitions = [('主控', 'controller', 'orchestrate')]
    n = 4 if layout == 'research' else 1
    definitions += [(f'规划场{i}', 'planner', 'plan') for i in range(1, n+1)]
    definitions += [(f'试验场{i}', 'executor', 'experiment') for i in range(1, n+1)]
    definitions += [(f'分析场{chr(65+i)}', 'analyst', 'analyze') for i in range(3 if layout == 'research' else 1)]
    definitions += [('整理场', 'organizer', 'organize')]
    return [{'address': f'{domain} / {label}', 'domain': domain, 'role': role,
             'skill': 'camp-'+skill, 'slot': f'd{d+1}-{r+1}'}
            for d,domain in enumerate(domains) for r,(label,role,skill) in enumerate(definitions)]


def managed_block(existing, body):
    block = START + '\n' + body.strip() + '\n' + END
    if START in existing or END in existing:
        if existing.count(START) != 1 or existing.count(END) != 1:
            raise ValueError('Malformed Basecamp managed block')
        before, rest = existing.split(START, 1)
        current, after = rest.split(END, 1)
        if (START+current+END).replace('\r\n','\n') != block:
            raise ValueError('Existing Basecamp block was edited; merge it deliberately')
        return existing
    return existing.rstrip() + ('\n\n' if existing.strip() else '') + block + '\n'


def install(root, name=None, domains=None, layout=None, dry_run=False):
    root = Path(root).expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise ValueError('Target is not a directory')
    code, templates, launcher = kit_paths()
    current_path = root / '.agents/basecamp/project.json'
    previous = json.loads(current_path.read_text(encoding='utf-8')) if current_path.exists() else None
    domains = domains or (previous['domains'] if previous else ['Main'])
    layout = layout or (previous['layout'] if previous else 'research')
    name = name or (previous['name'] if previous else root.name)
    if len(set(domains)) != len(domains) or not domains:
        raise ValueError('Domains must be nonempty and unique')
    if any(not d.strip() or len(d)>60 or re.search(r'[/\\\x00-\x1f]',d) for d in domains):
        raise ValueError('Invalid domain name')
    config = {'schema': 'codex-basecamp.project.v1', 'kit_version': VERSION, 'name': name,
              'domains': domains, 'layout': layout, 'roles': role_specs(domains,layout),
              'model_policy': 'inherit', 'permission_policy': 'inherit', 'business_calls_authorized': 0}
    files = {}
    for p in sorted(code.glob('*.py')):
        if p.name != 'run.py': files['.agents/basecamp/'+p.name] = p.read_bytes()
    files['.agents/basecamp/run.py'] = launcher.read_bytes()
    for p in sorted(templates.rglob('*')):
        if p.is_file():
            rel=p.relative_to(templates).as_posix()
            files['.agents/basecamp/templates/'+rel]=p.read_bytes()
            if rel.startswith('skills/'):
                files['.agents/'+rel]=p.read_bytes()
    for filename in ('WORKFLOW.md','README.md','START.md'):
        files['.agents/basecamp/'+filename]=(templates/filename).read_bytes()
    files['.agents/basecamp/project.json']=(json.dumps(config,ensure_ascii=False,indent=2)+'\n').encode()
    files['.agents/README.md'] = b'# Project collaboration\n\nSee [Basecamp](basecamp/README.md).\n'
    files['.agents/memory/README.md'] = '# 项目经验\n\n按业务域建立职责索引，按需读取。没有已形成经验时不创建占位经验条目。\n'.encode()
    # Existing project-owned entry points remain authoritative and unmodified.
    for rel in ('.agents/README.md','.agents/memory/README.md'):
        if (root/rel).exists(): files.pop(rel)
    if not (root/'README.md').exists():
        files['README.md']=(f'# {name}\n\n项目目标由用户定义。协作启动见 [.agents/basecamp/START.md](.agents/basecamp/START.md)。\n').encode()
    for filename,body in [('AGENTS.md',(templates/'AGENTS.md').read_text(encoding='utf-8')),
                          ('.gitignore','.codex-basecamp/\n__pycache__/\n*.py[cod]\n')]:
        path=root/filename
        existing=path.read_bytes().decode('utf-8-sig') if path.exists() else ''
        if filename=='.gitignore':
            marker='# codex-basecamp runtime'
            text=existing if marker in existing else existing.rstrip()+'\n\n'+marker+'\n'+body
        else:
            text=managed_block(existing,body)
        files[filename]=text.encode('utf-8') if text!=existing or not path.exists() else path.read_bytes()
    conflicts=[]
    for rel,data in files.items():
        path=root/rel
        # Reject directory links inside the target so the kit cannot escape it.
        for ancestor in [path,*path.parents]:
            if ancestor==root:break
            if ancestor.is_symlink() or (hasattr(ancestor,'is_junction') and ancestor.is_junction()):
                raise ValueError('Linked installation path: '+rel)
        if path.exists() and (not path.is_file() or path.read_bytes()!=data) and rel not in ('AGENTS.md','.gitignore'):
            conflicts.append(rel)
    if conflicts:
        raise ValueError('Existing files differ; nothing installed: '+', '.join(conflicts))
    manifest={'schema':'codex-basecamp.install.v1','version':VERSION,'files':[
        {'path':rel,'sha256':hashlib.sha256(data).hexdigest()} for rel,data in sorted(files.items())
        if rel.startswith(('.agents/basecamp/','.agents/skills/'))]}
    files['.agents/basecamp/install-manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    changed=[rel for rel,data in files.items() if not (root/rel).is_file() or (root/rel).read_bytes()!=data]
    if not dry_run:
        for rel in changed:
            path=root/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(files[rel])
    return {'state':'preview' if dry_run else 'files_ready','root':str(root),'roles_planned':len(config['roles']),
            'changed_files':changed,'threads_created':0,'next':'doctor, then provision only when the user requests new shared-project roles'}
