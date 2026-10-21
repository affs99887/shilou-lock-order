"""Consumer profile encrypted with user-bound DPAPI, never plaintext on disk."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile

MAGIC = b"PurchaseAssistant-DPAPI-v1\0"


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data, decrypt=False):
    """UI forbidden, current user scope. Do not use CRYPTPROTECT_LOCAL_MACHINE."""
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, result = DataBlob(len(data), buffer), DataBlob()
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise RuntimeError("Windows 用户加密服务不可用；请使用原 Windows 账户")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


class WindowsVault:
    def __init__(self, directory, encrypt=None, decrypt=None):
        self.path = Path(directory) / "profile.dpapi"
        self.encrypt = encrypt or crypt
        self.decrypt = decrypt or (lambda data: crypt(data, decrypt=True))

    def load_profile(self):
        try:
            data = self.path.read_bytes()
            if not data.startswith(MAGIC) or len(data) > 65536:
                raise ValueError()
            value = json.loads(self.decrypt(data[len(MAGIC):]).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except FileNotFoundError:
            raise RuntimeError("请先填写并保存你自己的收货资料") from None
        except Exception:
            raise RuntimeError("无法解密已存资料；请使用保存时的 Windows 账户或重新保存") from None

    def write_profile(self, value):
        temporary = None
        try:
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            encrypted = self.encrypt(payload)
            if json.loads(self.decrypt(encrypted).decode("utf-8")) != value:
                raise ValueError()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix="profile-", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(MAGIC + encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            if self.load_profile() != value:
                raise ValueError()
        except Exception:
            raise ValueError("资料未通过 Windows 加密回读校验；未确认保存成功") from None
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
        return {"saved": True}

    def delete_profile(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            raise ValueError("加密资料删除未确认，请稍后重试") from None
        return {"deleted": True}
