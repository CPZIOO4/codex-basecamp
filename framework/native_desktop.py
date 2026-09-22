"""Thin client for the desktop native tools; no scheduler or execution ownership."""
import json, os, queue, shutil, subprocess, threading
from pathlib import Path
from collections import deque

SERVER_RELATIVE = Path('app/resources/plugins/openai-bundled/plugins/codex-app-tools/server.mjs')
PACKAGE_QUERY = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$packages = @(Get-AppxPackage -Name OpenAI.Codex | ForEach-Object {
    [pscustomobject]@{Name=$_.Name; Version=$_.Version.ToString();
        InstallLocation=$_.InstallLocation; Status=$_.Status.ToString();
        IsFramework=$_.IsFramework; IsResourcePackage=$_.IsResourcePackage}
})
$running = @(Get-Process -Name Codex -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.Path) { $_.Path }
})
[pscustomobject]@{packages=$packages; running_paths=$running} | ConvertTo-Json -Depth 4 -Compress
'''


def installed_packages():
    """Query the current user's registered package; never enumerate WindowsApps."""
    try:
        answer = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', PACKAGE_QUERY],
            capture_output=True, text=True, encoding='utf-8', timeout=10,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('phase=package_query; ' + str(exc)) from exc
    if answer.returncode:
        raise RuntimeError('phase=package_query; exit_code=' + str(answer.returncode)
                           + '; stderr=' + answer.stderr[-2000:])
    try:
        value = json.loads(answer.stdout.lstrip('\ufeff'))
        if not isinstance(value, dict) or not isinstance(value.get('packages'), list):
            raise ValueError('Expected registered package list')
        return value
    except (ValueError, TypeError) as exc:
        raise RuntimeError('phase=package_query; invalid JSON metadata') from exc


def resolve_desktop_server():
    explicit = os.environ.get('BASECAMP_DESKTOP_SERVER')
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError('Configured desktop tool server does not exist')
        return path
    if os.name != 'nt':
        raise RuntimeError('Desktop discovery currently supports Windows; set BASECAMP_DESKTOP_SERVER for an independently verified adapter')
    metadata = installed_packages()
    eligible = []
    for package in metadata['packages']:
        if (package.get('Name') != 'OpenAI.Codex'
                or str(package.get('Status')).lower() not in ('ok', '0')
                or package.get('IsFramework') or package.get('IsResourcePackage')):
            continue
        location = package.get('InstallLocation')
        if not location or not Path(location).is_absolute():
            continue
        try:
            version = tuple(int(part) for part in package['Version'].split('.'))
            if len(version) != 4:
                continue
            root = Path(location)
            server = root / SERVER_RELATIVE
            with server.open('rb') as stream:
                stream.read(1)
        except (OSError, ValueError, KeyError):
            continue
        running = any(Path(path).is_relative_to(root)
                      for path in metadata.get('running_paths', []) if path)
        eligible.append((running, version, str(server)))
    if not eligible:
        raise RuntimeError('phase=package_resolve; no healthy registered OpenAI.Codex package with readable server.mjs')
    return Path(max(eligible)[2])

class Desktop:
    def __init__(self, main_thread, server=None):
        if os.environ.get('CODEX_THREAD_ID') != main_thread:
            raise RuntimeError('Caller identity differs from native tool connection')
        if not os.environ.get('CODEX_APP_TOOLS_PIPE_PATH'):
            raise RuntimeError('Native desktop connection unavailable')
        self.main_thread = main_thread
        automatic = server is None or server == 'auto'
        for attempt in range(2):
            self.server_path = resolve_desktop_server() if automatic else Path(server)
            try:
                self._connect()
                return
            except Exception as exc:
                process = getattr(self, 'process', None)
                if process is not None:
                    try:
                        self.close()
                    except Exception:
                        pass
                code = process.poll() if process is not None else None
                detail = ''.join(getattr(self, 'stderr_tail', []))[-2000:]
                # This connection has never sent a task. Retry only a vanished entry.
                vanished = False
                try:
                    with self.server_path.open('rb') as stream:
                        stream.read(1)
                except FileNotFoundError:
                    vanished = True
                except OSError:
                    pass
                if automatic and attempt == 0 and vanished:
                    continue
                raise RuntimeError('phase=desktop_initialize; server=' + str(self.server_path)
                                   + '; exit_code=' + str(code) + '; ' + str(exc)
                                   + '; stderr=' + detail) from exc

    def _connect(self):
        self.process = None
        self.lock = threading.Lock()
        self.pending = {}
        self.seq = 0
        self.stderr_tail = deque(maxlen=16)
        node = os.environ.get('CODEX_MCP_NODE_PATH') or shutil.which('node')
        if not node:
            raise RuntimeError('phase=process_start; Node runtime unavailable')
        self.process = subprocess.Popen([node, str(self.server_path)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self.stderr_reader.start()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
            'clientInfo': {'name': 'codex-basecamp', 'version': '1'}})
        self.tool_schemas = self.rpc('tools/list', {})['tools']
        self.names = {t['name'] for t in self.tool_schemas}

    def _read_stderr(self):
        for line in self.process.stderr:
            self.stderr_tail.append(line.decode('utf-8', errors='replace'))

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                with self.lock:
                    target = self.pending.get(value.get('id'))
                if target is not None:
                    target.put(value)
        finally:
            with self.lock:
                for target in self.pending.values():
                    target.put({'error': 'Native desktop MCP closed'})

    def rpc(self, method, params, timeout=60):
        target = queue.Queue()
        with self.lock:
            self.seq += 1
            request_id = self.seq
            self.pending[request_id] = target
            body = {'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params}
            try:
                self.process.stdin.write((json.dumps(body, ensure_ascii=False)+'\n').encode('utf-8'))
                self.process.stdin.flush()
            except BaseException:
                self.pending.pop(request_id, None)
                raise
        try:
            try:
                value = target.get(timeout=timeout)
            except queue.Empty as exc:
                raise TimeoutError('Native desktop RPC timed out: '+method) from exc
            if 'error' in value:
                raise RuntimeError(str(value['error']))
            return value['result']
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def call(self, name, arguments, timeout=60):
        if name not in self.names:
            raise ValueError('Native tool unavailable: '+name)
        value = self.rpc('tools/call', {'name': name, 'arguments': arguments,
            '_meta': {'openai/threadId': self.main_thread}}, timeout)
        if value.get('isError'):
            raise RuntimeError(str(value))
        return json.loads(next(c['text'] for c in value['content'] if c['type'] == 'text'))

    def close(self):
        if self.process is None:
            return
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()  # Only the helper this instance started.
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        for name in ('reader', 'stderr_reader'):
            thread = getattr(self, name, None)
            if thread:
                thread.join(timeout=1)
        self.process.stdout.close()
        self.process.stderr.close()
