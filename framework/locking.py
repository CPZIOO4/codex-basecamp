"""Per-recipient file locks; independent recipients can proceed concurrently."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import re
import time

if os.name == 'nt':
    import msvcrt
else:
    import fcntl


@contextmanager
def file_lock(directory, key, timeout=120):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', key):
        raise ValueError('Unsafe lock key')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / (key + '.lock')).open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError('Lock busy: ' + key) from exc
                time.sleep(0.05)
        try:
            yield directory / (key + '.json')
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
