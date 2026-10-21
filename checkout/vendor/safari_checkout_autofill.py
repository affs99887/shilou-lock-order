"""Consumer-only Keychain backend; never import the personal legacy helper."""
import getpass
import json
import re
import shlex
import subprocess
import sys

from platform_runtime import data_directory

KEYCHAIN_SERVICE = "local.purchase-assistant.consumer.profile.v1"
KEYCHAIN_ACCOUNT = getpass.getuser()
WINDOWS_DIRECTORY = None


def windows_vault():
    from windows_vault import WindowsVault
    return WindowsVault(WINDOWS_DIRECTORY or data_directory())


def load_profile():
    if sys.platform == "win32":
        return windows_vault().load_profile()
    r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
                        "-a", KEYCHAIN_ACCOUNT, "-w"], capture_output=True, text=True, timeout=20)
    if r.returncode:
        raise RuntimeError("请先填写并保存你自己的收货资料")
    value = r.stdout.strip()
    if not value.startswith("{") and re.fullmatch(r"(?:[0-9a-fA-F]{2})+", value):
        value = bytes.fromhex(value).decode("utf-8")
    result = json.loads(value)
    if not isinstance(result, dict):
        raise RuntimeError("资料格式错误，请重新保存")
    return result


def write_profile(value):
    if sys.platform == "win32":
        return windows_vault().write_profile(value)
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    args = ["add-generic-password", "-U", "-s", KEYCHAIN_SERVICE,
            "-a", KEYCHAIN_ACCOUNT, "-w", payload]
    try:
        r = subprocess.run(["security", "-i"], input=shlex.join(args)+"\n",
                           capture_output=True, text=True, timeout=20)
        if r.returncode or load_profile() != value:
            raise ValueError()
    except Exception:
        raise ValueError("资料未通过钥匙串回读校验；请解锁钥匙串后重试") from None
    return {"saved": True}


def delete_profile():
    if sys.platform == "win32":
        return windows_vault().delete_profile()
    r = subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE,
                        "-a", KEYCHAIN_ACCOUNT], capture_output=True, text=True, timeout=20)
    if r.returncode not in (0, 44):
        raise ValueError("钥匙串删除未确认，请检查系统提示")
    return {"deleted": True}


def load_payment_push_url():
    return ""  # No third-party notification service in this preview.


def send_payment_push(*args):
    raise RuntimeError("消费者预览版未提供手机推送")
