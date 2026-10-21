from pathlib import Path
import os
import sys

def application_data_root():
    if sys.platform == 'win32':
        root = os.environ.get('LOCALAPPDATA')
        if not root:
            raise RuntimeError('无法找到 Windows 用户资料目录')
        return Path(root)
    return Path.home() / 'Library/Application Support'

def data_directory():
    name = 'ApplePickupWatcherInternational' if os.environ.get('APW_CHECKOUT_VARIANT') == 'international' else 'ApplePickupWatcherAuto'
    return application_data_root() / name

def lock_file(stream):
    if sys.platform == 'win32':
        import msvcrt
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
