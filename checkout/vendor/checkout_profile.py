"""Explicit local profile editor. Never include profile values in diagnostics."""
import json
import re
import shlex
import subprocess
import sys
from safari_checkout_autofill import load_profile, KEYCHAIN_ACCOUNT, KEYCHAIN_SERVICE

FIELDS = ("full_name", "family_name", "given_name", "phone", "email", "province",
          "city", "district", "address", "postal_code", "id_last4", "invoice_title", "tax_id",
          "address2", "family_name_kana", "given_name_kana")
REQUIRED = ("family_name", "given_name", "phone", "email", "province", "city",
            "district", "address", "postal_code", "id_last4")
LABELS = dict(zip(REQUIRED, ("姓氏", "名字", "手机号", "邮箱", "省份", "城市",
                            "区县", "详细地址", "邮编", "证件后四位")))


def editable_profile(locale='zh_CN'):
    if locale != 'zh_CN':
        raw = regional_vault(locale)
        return {k: str(raw.get(k, '')) for k in FIELDS}
    try:
        raw = load_profile()
    except Exception:
        raise ValueError("无法读取已保存资料，请填写后保存；Mac 如有系统提示，请解锁钥匙串并允许读取") from None
    return {k: str(raw.get(k, "")) for k in FIELDS}


def validate_profile(raw, require_pickup=True, locale='zh_CN'):
    if locale != 'zh_CN':
        return validate_regional_profile(raw, locale)
    if not isinstance(raw, dict) or set(raw) - set(FIELDS):
        raise ValueError("资料字段不支持；此处不接收账号密码或验证码")
    if any(not isinstance(v, str) or len(v) > 500 or any(ord(c) < 32 for c in v) for v in raw.values()):
        raise ValueError("资料必须是单行文本，单项不超过 500 字")
    p = {k: raw.get(k, "").strip() for k in FIELDS}
    for k in REQUIRED:
        if k == "id_last4" and not require_pickup:
            continue
        if not p[k]:
            raise ValueError("请填写" + LABELS[k])
    phone = re.sub(r"[\s()-]", "", p["phone"])
    if phone.startswith("+86"):
        phone = phone[3:]
    elif len(phone) == 13 and phone.startswith("86"):
        phone = phone[2:]
    if not re.fullmatch(r"1[0-9]{10}", phone):
        raise ValueError("手机号须为 11 位大陆手机号")
    p["phone"] = phone
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", p["email"]):
        raise ValueError("邮箱格式不正确")
    if not re.fullmatch(r"[0-9]{6}", p["postal_code"]):
        raise ValueError("邮编须为 6 位数字")
    if (require_pickup or p["id_last4"]) and not re.fullmatch(r"[0-9]{3}[0-9Xx]", p["id_last4"]):
        raise ValueError("证件后四位须为文本，保留前导零")
    p["id_last4"] = p["id_last4"].upper()
    p["full_name"] = p["full_name"] or p["family_name"] + p["given_name"]
    return p


def store_profile(raw, locale='zh_CN'):
    if locale != 'zh_CN':
        p = validate_profile(raw, locale=locale)
        regional_vault(locale, p)
        return {'saved': True}
    p = validate_profile(raw)
    if sys.platform == 'win32':
        from safari_checkout_autofill import write_profile
        return write_profile(p)
    # Preserve unrelated legacy settings. Never place personal data in argv.
    try:
        merged = dict(load_profile())
    except RuntimeError:
        merged = {}
    merged.update(p)
    payload = json.dumps(merged, ensure_ascii=False, separators=(",", ":"))
    args = ["add-generic-password", "-U", "-s", KEYCHAIN_SERVICE,
            "-a", KEYCHAIN_ACCOUNT, "-w", payload]
    try:
        result = subprocess.run(["security", "-i"], input=shlex.join(args) + "\n",
                                text=True, capture_output=True, timeout=20)
    except Exception:
        raise ValueError("钥匙串写入未确认；请解锁钥匙串后重试") from None
    # Interactive security can exit 0 after an individual command fails.
    try:
        actual = load_profile()
        matches = all(str(actual.get(k, "")) == v for k, v in p.items())
    except Exception:
        matches = False
    if result.returncode or not matches:
        raise ValueError("钥匙串保存未通过回读校验；未声明保存成功")
    return {"saved": True}


def regional_vault(locale, value=None):
    """Each storefront has its own encrypted profile, never domestic fallback."""
    from regional_checkout import region
    region(locale)
    if sys.platform == 'win32':
        from safari_checkout_autofill import windows_vault
        from windows_vault import WindowsVault
        vault = WindowsVault(windows_vault().path.parent / locale)
        if value is None:
            return vault.load_profile()
        vault.write_profile(value)
        if vault.load_profile() != value:
            raise ValueError('本地区资料保存核验失败')
        return value
    service = KEYCHAIN_SERVICE + '.region.' + locale
    if value is None:
        result = subprocess.run(['security', 'find-generic-password', '-s', service,
                                 '-a', KEYCHAIN_ACCOUNT, '-w'], text=True, capture_output=True, timeout=20)
        if result.returncode:
            raise ValueError('请先为本地区填写并保存联系资料')
        payload = result.stdout.strip()
        if not payload.startswith('{') and re.fullmatch(r'(?:[0-9a-fA-F]{2})+', payload):
            payload = bytes.fromhex(payload).decode('utf-8')
        try:
            raw = json.loads(payload)
            if not isinstance(raw, dict):
                raise ValueError()
            return raw
        except (ValueError, TypeError):
            raise ValueError('本地区资料损坏，请重新保存') from None
    args = ['add-generic-password', '-U', '-s', service, '-a', KEYCHAIN_ACCOUNT,
            '-w', json.dumps(value, ensure_ascii=False, separators=(',', ':'))]
    result = subprocess.run(['security', '-i'], input=shlex.join(args) + '\n',
                            text=True, capture_output=True, timeout=20)
    if result.returncode or regional_vault(locale) != value:
        raise ValueError('本地区钥匙串保存未通过回读校验')
    return value


def validate_regional_profile(raw, locale):
    from regional_checkout import region
    r = region(locale)
    if not isinstance(raw, dict) or set(raw) - set(FIELDS):
        raise ValueError('资料字段不支持；不接收密码、验证码或银行卡')
    if any(not isinstance(v, str) or len(v) > 500 or any(ord(c) < 32 for c in v) for v in raw.values()):
        raise ValueError('资料须为单行文本，不超过 500 字')
    p = {k: raw.get(k, '').strip() for k in FIELDS}
    for key in ('family_name', 'given_name', 'phone', 'email', *r['required']):
        if not p[key]:
            raise ValueError('本地区资料缺少：' + LABELS.get(key, {'family_name_kana':'姓氏假名', 'given_name_kana':'名字假名'}.get(key, key)))
    phone = re.sub(r'[\s()-]', '', p['phone'])
    if phone.startswith('+' + r['phonePrefix']):
        phone = phone[len(r['phonePrefix']) + 1:]
        if locale in ('zh_TW', 'ja_JP', 'en_AU', 'en_MY') and not phone.startswith('0'):
            phone = '0' + phone
    if not re.fullmatch(r['phonePattern'], phone):
        raise ValueError('电话号码不符合' + r['title'] + '格式，请使用当地号码')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', p['email']):
        raise ValueError('邮箱格式不正确')
    if r['postalPattern'] and not re.fullmatch(r['postalPattern'], p['postal_code']):
        raise ValueError('邮编不符合' + r['title'] + '格式')
    p.update(phone=phone, full_name=p['full_name'] or p['family_name'] + ' ' + p['given_name'])
    # The mainland identity suffix must never leak into foreign forms.
    p['id_last4'] = ''
    return p


def normalized_regional_profile(locale):
    return validate_profile(editable_profile(locale), locale=locale)
