"""Portable HTTPS trust: explicit packaged roots, never a developer CA path."""
import hashlib
from pathlib import Path
import ssl
import sys
import certifi


def certificate_bundle():
    path = Path(certifi.where()).resolve()
    if getattr(sys, "frozen", False):
        root = Path(sys._MEIPASS).resolve()
        roots = [root]
        # macOS PyInstaller separates data into Resources and symlinks it
        # from Frameworks. Resolve the real file, but allow only this app.
        if root.name == "Frameworks" and root.parent.name == "Contents":
            roots.append((root.parent / "Resources").resolve())
        if not any(path.is_relative_to(directory) for directory in roots):
            raise RuntimeError("证书文件不在软件包内，请重新安装完整安装包")
    if not path.is_file():
        raise RuntimeError("软件证书文件缺失，请重新安装完整安装包")
    return path


def https_context():
    # Passing an explicit cafile avoids SSL_CERT_FILE/SSL_CERT_DIR and the
    # OpenSSL compile-time Homebrew locations. Hostname verification stays on.
    context = ssl.create_default_context(cafile=str(certificate_bundle()))
    context.set_alpn_protocols(["http/1.1"])
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("HTTPS 安全校验配置错误")
    return context


def self_test(live=False):
    # Fail the test if any code attempts to load machine-default certificates.
    original = ssl.SSLContext.load_default_certs
    def forbidden(*args, **kwargs):
        raise AssertionError("machine-default certificate dependency")
    ssl.SSLContext.load_default_certs = forbidden
    try:
        context = https_context()
        count = context.cert_store_stats()["x509_ca"]
        assert count > 50
        report = {"passed":True, "packaged_roots":True, "ca_count":count,
                  "hostname_verified":context.check_hostname,
                  "certificate_required":context.verify_mode == ssl.CERT_REQUIRED,
                  "default_store_used":False, "certifi_version":certifi.__version__,
                  "ca_sha256":hashlib.sha256(certificate_bundle().read_bytes()).hexdigest()}
        if live:
            from address_regions import AppleRegions
            data = AppleRegions().lookup()
            report["official_province_count"] = len(data["state"])
        return report
    finally:
        ssl.SSLContext.load_default_certs = original
