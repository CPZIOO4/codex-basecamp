"""Project-local configuration; never migrate an account or a source project's IDs."""
import json
import os
from pathlib import Path
import shutil


def project_config(root):
    return json.loads((Path(root).resolve() / '.agents/basecamp/project.json').read_text(encoding='utf-8'))


def state_root(root):
    return Path(root).resolve() / '.codex-basecamp'


def resolve_cli(local=None):
    explicit = (local or {}).get('cli_path') or os.environ.get('BASECAMP_CODEX_CLI')
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise RuntimeError('Configured Codex CLI is not a file')
        if path.suffix.lower() == '.js':
            node = shutil.which('node')
            if not node:
                raise RuntimeError('Node is required for the configured Codex JS entry')
            return [node, str(path)]
        if os.name == 'nt' and path.suffix.lower() != '.exe':
            raise RuntimeError('Set BASECAMP_CODEX_CLI to codex.exe or bin/codex.js')
        return [str(path)]
    binary = shutil.which('codex.exe' if os.name == 'nt' else 'codex')
    if binary:
        return [binary]
    # npm's Windows shims are not executables; invoke its JS entry without a shell.
    for name in ('codex.cmd', 'codex.ps1', 'codex'):
        shim = shutil.which(name)
        if shim:
            entry = Path(shim).parent / 'node_modules/@openai/codex/bin/codex.js'
            if entry.is_file() and shutil.which('node'):
                return [shutil.which('node'), str(entry)]
    raise RuntimeError('Codex CLI not found; install it or set BASECAMP_CODEX_CLI')


def local_config(root):
    path = state_root(root) / 'config.local.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
