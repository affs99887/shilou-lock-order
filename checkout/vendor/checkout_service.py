#!/usr/bin/env python3
"""Local, serial Safari checkout runner. Default launch is DISARMED.

Secrets remain in Keychain. No request logs, raw URLs, DOM or contact values
are persisted. --probe performs only a public-page read, never a checkout.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
try:
    import fcntl
except ImportError:  # Windows consumer runner supplies its own native lock.
    fcntl = None
import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from checkout_engine import (Attempt, CATALOG, DEFAULTS, CircuitBreaker,
                             retry_after_seconds, validate_preferences, variants, purchase_targets)
from safari_checkout_autofill import load_profile, load_payment_push_url, send_payment_push
from checkout_profile import editable_profile, validate_profile, store_profile

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / ".apple_checkout_v2.json"
EVENTS = ROOT / ".apple_checkout_events.jsonl"
LOCK = ROOT / ".apple_checkout_v2.lock"
RUNTIME = ROOT / ".apple_checkout_runtime.json"
PREPARATION = ROOT / ".apple_checkout_preparation.json"
CONTROLS = {"start", "pause", "stop", "continue", "local_check", "login", "session_check",
            "product_check", "bag_test", "rehearse_delivery", "rehearse_pickup", "rehearse"}
PURCHASE_TASKS = {"bag_test", "rehearse_delivery", "rehearse_pickup", "rehearse"}
JS = (ROOT / "checkout_browser.js").read_text(encoding="utf-8")
ADAPTER_VERSION = hashlib.sha256(JS.encode("utf-8")).hexdigest()


def atomic_json(path: Path, data):
    temp = path.with_suffix(".tmp")
    with open(temp, "w", encoding="utf-8") as f:
        os.chmod(temp, 0o600)
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp, path)


def load_preferences():
    if CONFIG.exists():
        return validate_preferences(json.loads(CONFIG.read_text(encoding="utf-8")))
    p = copy.deepcopy(DEFAULTS)
    old = ROOT / ".apple_purchase_preferences.json"
    if old.exists():
        try:
            budget = json.loads(old.read_text()).get("max_price", 20000)
            if type(budget) is int and 1 <= budget <= 100000:
                p["max_price"] = budget
        except (ValueError, OSError):
            pass
    atomic_json(CONFIG, p)
    return p


def normalized_profile(require_pickup=True):
    p = load_profile()
    phone = re.sub(r"\D", "", str(p.get("phone", "")))
    if len(phone) == 13 and phone.startswith("86"):
        phone = phone[2:]
    if not re.fullmatch(r"1\d{10}", phone):
        raise ValueError("钥匙串手机号不是有效的 11 位大陆号码")
    p["phone"] = phone
    suffix = str(p.get("id_last4", ""))
    if (require_pickup or suffix) and not re.fullmatch(r"[0-9]{3}[0-9Xx]", suffix):
        raise ValueError("请配置证件后四位，保留前导零")
    for key in ("family_name", "given_name", "email", "province", "city", "district", "address", "postal_code"):
        if not p.get(key):
            raise ValueError("请先配置资料字段：" + key)
    # Old dates, trade-in, and AppleCare do not control this new runner.
    return {k: str(p[k]) for k in ("full_name","family_name","given_name","phone","email",
                                  "province","city","district","address","postal_code","id_last4",
                                  "invoice_title","tax_id") if k in p}


class Safari:
    """Dedicated checkout tab, allowing Safari's inherited pinned tabs.

    The generated AppleScript is supplied on stdin, not command-line arguments,
    so profile values never appear in process listings. Subprocess errors are
    replaced with fixed messages (AppleScript errors can quote source/secrets).
    """
    def __init__(self):
        self.window = 0
        self.windows = []
        self.bindings = {}
        self.marker = "apple-checkout-v2-" + uuid.uuid4().hex
        self.expected_path = ""
        self.adapter_windows = set()

    def script(self, source, timeout=12):
        try:
            r = subprocess.run(["osascript", "-"], input=source, text=True,
                               capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Safari 通信等待超过时限，已保留页面；请收起 Safari 菜单或弹窗后继续") from None
        if r.returncode:
            if "-1743" in r.stderr:
                raise RuntimeError("请在系统设置 → 隐私与安全性 → 自动化中，允许抢先购控制 Safari")
            if "JavaScript from Apple Events" in r.stderr or "来自 Apple 事件" in r.stderr:
                raise RuntimeError("Safari 权限未开启：设置 → 高级 → 显示网页开发者功能，再在开发菜单／开发者设置中勾选“允许来自 Apple 事件的 JavaScript”，回到软件重新检查")
            raise RuntimeError("Safari 自动化不可用：请检查 Apple 事件 JavaScript 权限或浏览器状态")
        return r.stdout.strip()

    def open(self, url, foreground=True):
        # Only preflight may replace a lost window. Mid-checkout calls still
        # fail closed, preserving the cart/action ledger for manual handling.
        if self.window and self.script(self.bound('return "BOUND"')) == "LOST_TAB":
            self.forget_entry_window(self.window)
        if self.window and url == "https://www.apple.com.cn/shop/bag":
            if self.call({}, {}).get("stage") == "BAG":
                if foreground:
                    self.present()
                return  # Already at the live cart: don't invalidate its document.
        if not self.window:
            self.marker = "apple-checkout-v2-" + uuid.uuid4().hex
            marker_url = "about:blank#" + self.marker
            result = self.script('tell application "Safari"\n'
                'set previousWindowIDs to id of windows\n'
                'set previousWindow to 0\nif (count of windows) > 0 then set previousWindow to id of front window\n'
                'set markerURL to ' + json.dumps(marker_url) + '\n'
                'make new document with properties {URL:markerURL}\n'
                'repeat 30 times\n'
                'repeat with candidate in windows\n'
                'if (id of candidate) is not in previousWindowIDs then\n'
                'repeat with tabIndex from 1 to (count of tabs of candidate)\n'
                'if (URL of tab tabIndex of candidate) is markerURL then\n'
                'set resultText to (id of candidate as text) & linefeed & (tabIndex as text)\n'
                'repeat with sibling in tabs of candidate\n'
                'set siblingURL to URL of sibling\n'
                'if siblingURL is missing value then set siblingURL to ""\n'
                'set resultText to resultText & linefeed & siblingURL\n'
                'end repeat\n' + ('' if foreground else 'if previousWindow is not 0 and (exists window id previousWindow) then set index of window id previousWindow to 1\n') +
                'return resultText & linefeed & "END"\nend if\nend repeat\n'
                'end if\nend repeat\ndelay 0.05\nend repeat\nreturn "0"\nend tell', timeout=12)
            lines = result.split("\n")
            try:
                window, tab_index = int(lines[0]), int(lines[1])
                urls = lines[2:-1]
                if lines[-1] != "END" or not window or not 1 <= tab_index <= len(urls) or urls[tab_index - 1] != marker_url:
                    raise ValueError()
            except (ValueError, IndexError):
                raise RuntimeError("Safari 未确认新建选购标签页；已停止，不会接管其他网页") from None
            self.window = window
            # Keep sibling URLs only in memory, never logs. Pinned tabs are
            # neither navigated nor closed. Reordering/changes fail closed.
            self.bindings[window] = (tab_index, urls)
        self.navigate(url, foreground=foreground)

    def forget_entry_window(self, window):
        """Drop an invalid reference, without touching the unowned live page."""
        self.windows = [w for w in self.windows if w != window]
        self.bindings.pop(window, None)
        self.adapter_windows.discard(window)
        if self.window == window:
            self.window = 0

    def rotate_entry(self, url):
        """Pre-add entry only: discard stale standby references, never adopt tabs.

        Checkout continues to use strict rotate/navigate. A lost reference after
        a possible write must not silently start another purchase.
        """
        current = self.window
        candidates = list(dict.fromkeys(self.windows + ([current] if current else [])))
        valid = []
        for window in candidates:
            self.window = window
            if self.script(self.bound('return "BOUND"')) == "LOST_TAB":
                self.forget_entry_window(window)
            else:
                valid.append(window)
        self.windows = valid
        standby = next((w for w in valid if w != current), None)
        if standby is not None:
            self.window = standby
            self.navigate(url, foreground=False)
        else:
            self.window = 0
            self.open(url, foreground=False)
        # Keep only this entry pair; never close or navigate discarded windows.
        self.windows = list(dict.fromkeys(([current] if current in valid else []) + [self.window]))

    @staticmethod
    def entry_layout():
        """Use the visible main display (exclude Dock/menu); no AX permission."""
        from AppKit import NSScreen
        screen = NSScreen.mainScreen()
        rect = screen.visibleFrame()
        desktop_top = NSScreen.screens()[0].frame().size.height
        left, top = int(rect.origin.x), int(desktop_top - rect.origin.y - rect.size.height)
        width, height = int(rect.size.width), int(rect.size.height)
        mid = left + width // 2
        return [(left, top, mid - 3, top + height),
                (mid + 3, top, left + width, top + height)]

    def tile_entry_windows(self, windows):
        """Only explicitly bound entry windows; do not move other Safari pages."""
        owned = list(dict.fromkeys(windows))
        if len(owned) != 2:
            return False
        previous = self.window
        try:
            # Verify both before moving either, and recheck in each mutation.
            for window in owned:
                self.window = window
                if self.script(self.bound('return "BOUND"')) == "LOST_TAB":
                    return False
            for window, bounds in zip(owned, self.entry_layout()):
                self.window = window
                frame = '{' + ', '.join(str(n) for n in bounds) + '}'
                script = 'set bounds to ' + frame + '\n' + self.presentation_script()
                if self.script(self.bound(script)) == "LOST_TAB":
                    return False
            return True
        finally:
            self.window = previous

    def rotate(self, url):
        """At most two owned windows, serial navigation, never parallel carts."""
        if self.window and self.window not in self.windows:
            self.windows.append(self.window)
        if len(self.windows) < 2:
            self.window = 0
            self.open(url)
            self.windows.append(self.window)
            return
        candidate = next(w for w in self.windows if w != self.window)
        previous = self.window
        self.window = candidate
        if self.script(self.bound('return "BOUND"')) == "LOST_TAB":
            self.window = previous
            raise RuntimeError("备用专属窗口已关闭或标签变化；停止切换，不接管其他页面")
        self.navigate(url, foreground=True)

    @staticmethod
    def presentation_script():
        return ('set miniaturized to false\nset current tab to checkoutTab\n'
                'set index to 1\ntell application "Safari" to activate\n')

    def present(self):
        """Raise only the owned window on user-visible entry, not polling."""
        if self.script(self.bound(self.presentation_script())) == "LOST_TAB":
            raise RuntimeError("选购窗口或标签已变化，无法显示；没有切换到其他网页")

    def bound(self, content):
        if not self.window:
            raise RuntimeError("专属 Safari 窗口未创建")
        index, urls = self.bindings.get(self.window, (1, [""]))
        siblings = ''.join(f'if (URL of tab {i}) is not {json.dumps(url)} then return "LOST_TAB"\n'
                           for i, url in enumerate(urls, 1) if i != index)
        return ('tell application "Safari"\n'
                f'if not (exists window id {self.window}) then return "LOST_TAB"\n'
                f'tell window id {self.window}\n'
                f'if (count of tabs) is not {len(urls)} then return "LOST_TAB"\n' + siblings +
                f'set checkoutTab to tab {index}\n' + content + '\nend tell\nend tell')

    def navigate(self, url, foreground=False):
        p = urlparse(url)
        if p.scheme != "https" or p.hostname != "www.apple.com.cn" or not p.path.startswith("/shop/") or p.query:
            raise ValueError("只允许已知 Apple 中国商店入口")
        self.adapter_windows.discard(self.window)
        result = self.script(self.bound('try\ndo JavaScript "window.__appleCheckoutDeparting = true" in checkoutTab\nend try\n'
                                        'set URL of checkoutTab to ' + json.dumps(url) + '\n' +
                                        (self.presentation_script() if foreground else '')))
        self.expected_path = p.path
        if result == "LOST_TAB":
            raise RuntimeError("专属窗口被关闭或标签数量变化，已暂停")

    @staticmethod
    def adapter_code(payload, install=False):
        """Cache code, never page state, credentials or an action result.

        Each call still rebuilds the snapshot and validates fingerprint/nonce.
        A missing document-local adapter proves that this call did no action;
        only that exact result permits installing and trying once. Timeouts,
        malformed replies and unknown writes must never trigger a replay.
        """
        version = json.dumps(ADAPTER_VERSION)
        missing = json.dumps({"adapter_missing": ADAPTER_VERSION}, separators=(",", ":"))
        setup = ("window.__qxgDocumentAdapter={version:" + version + ",run:(function(){\n" +
                 JS + "\nreturn appleCheckout;})()};") if install else (
                 "return " + json.dumps(missing) + ";")
        return ("(()=>{let a=window.__qxgDocumentAdapter;"
                "if(!a||a.version!==" + version + "||typeof a.run!=='function'){" + setup +
                "a=window.__qxgDocumentAdapter;}"
                "return JSON.stringify(a.run(" + json.dumps(payload, ensure_ascii=False) + "));})()")

    def call(self, profile, target, command=None):
        payload = {"profile": profile, "target": target, "expected_path": self.expected_path}
        if command:
            payload["command"] = command
        install = self.window not in self.adapter_windows
        for _ in range(2):
            code = self.adapter_code(payload, install=install)
            result = self.script(self.bound('do JavaScript ' + json.dumps(code, ensure_ascii=False) + ' in checkoutTab'))
            if result == "LOST_TAB":
                self.adapter_windows.discard(self.window)
                return {"stage": "LOST_TAB"}
            try:
                parsed = json.loads(result)
                if not isinstance(parsed, dict):
                    if command:
                        raise RuntimeError("Safari 操作回执无法确认，已保留现场；不会重复执行")
                    return {"stage": "LOADING"}
            except (ValueError, TypeError):
                if command:
                    raise RuntimeError("Safari 操作回执无法确认，已保留现场；不会重复执行") from None
                return {"stage": "LOADING"}
            if parsed == {"adapter_missing": ADAPTER_VERSION} and not install:
                self.adapter_windows.discard(self.window)
                install = True
                continue
            if parsed.get("stage") not in (None, "LOADING"):
                self.expected_path = ""
            if parsed.get("stage") != "LOADING":
                self.adapter_windows.add(self.window)
            # Clicks can navigate to a fresh document. Install on the next read
            # in the same trip instead of paying a cache miss at every page.
            if command and command.get("kind") == "click":
                self.adapter_windows.discard(self.window)
            return parsed
        return {"stage": "LOADING"}


class Service:
    def __init__(self, browser=None, preferences=None, persist=True):
        self.lock = threading.RLock()
        self.commands = queue.Queue()
        self.pushes = queue.Queue()
        self.push_generation = 0
        self.stop_event = threading.Event()
        self.browser = browser or Safari()
        self.preferences = preferences or load_preferences()
        self.persist = persist
        self.breaker = CircuitBreaker()
        self.history = []
        self.run_id = uuid.uuid4().hex[:12]
        if persist:
            if EVENTS.exists():
                # All v2 events are allowlisted, redacted summaries.
                with EVENTS.open('rb') as f:
                    f.seek(0,2);size=f.tell();f.seek(max(0,size-4_000_000))
                    if size>4_000_000:f.readline()
                    lines=f.read().decode('utf-8',errors='replace').splitlines()[-200:]
                for line in lines:
                    try:
                        entry = json.loads(line)
                        if isinstance(entry, dict):
                            self.history.append(entry)
                    except ValueError:
                        pass
            if RUNTIME.exists():
                try:
                    runtime = json.loads(RUNTIME.read_text(encoding="utf-8"))
                    self.breaker.until = float(runtime.get("cooldown_until",0))
                    self.breaker.failures = int(runtime.get("failures",0))
                except (ValueError,TypeError,OSError):
                    pass
        self.attempt = None
        self.profile = {}
        self.push_url = ""
        self.active = False
        self.index = 0
        self.phase = "IDLE"
        self.pending_remove = None
        self.phase_since = 0
        self.next_probe = 0
        self.recoveries = 0
        self.last_entry_request = 0
        self.retry_needs_cart = False
        self.inventory = []
        self.login_watch = False
        self.region_watch = False
        self.region_probe_at = 0
        self.region_candidate = None
        self.region_candidate_at = 0
        self.login_watch_until = 0
        self.login_probe_at = 0
        self.login_candidate = None
        self.login_candidate_count = 0
        self.prelogin_confirmations = 0
        self.job = "monitor"
        self.run_preferences = None
        self.task_started = 0
        self.task_finished = None
        self.pause_started = None
        self.paused_seconds = 0
        self.interventions = 0
        self.task_recorded = False
        self.preparation = {}
        if persist and PREPARATION.exists():
            try:
                self.preparation = json.loads(PREPARATION.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
        self.status = {"mode": "stopped", "stage": "IDLE", "reason": "未开始；先设置并点击开始备战",
                       "elapsed": 0, "acceptance": "未验证", "next_retry": 0,
                       "last_check": None, "pid": os.getpid(), "version": 2,
                       "job": "monitor", "session": "未检查（结账仍可能重新认证）",
                       "preparation": self.preparation, "results": [],
                       "session_checked_at": None, "session_window": None}
        if persist:
            for path in sorted(ROOT.glob("task-report-*.json"))[-30:]:
                try:
                    r = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(r, dict) and all(k in r for k in ("task","passed","reason","time","elapsed")):
                        self.status["results"].append({k:r[k] for k in
                            ("task","passed","reason","time","elapsed","timing") if k in r})
                except (ValueError,OSError):
                    pass

    @property
    def execution_preferences(self):
        return self.run_preferences or self.preferences

    def idle_required(self):
        if self.active or self.attempt is not None or self.status["mode"] == "ready":
            raise ValueError("请先停止当前任务；最终提交页面应保留，不要运行其他测试")

    def start_timing(self):
        self.task_started = time.monotonic()
        self.task_finished = None
        self.pause_started = None
        self.paused_seconds = 0
        self.interventions = 0

    def pause_timing(self):
        if self.task_started and self.task_finished is None and self.pause_started is None:
            self.pause_started = time.monotonic()
            self.interventions += 1

    def resume_timing(self):
        if self.pause_started is not None:
            self.paused_seconds += time.monotonic() - self.pause_started
            self.pause_started = None

    def timing(self):
        if not self.task_started:
            return {"total_seconds":0,"paused_seconds":0,"running_seconds":0,"interventions":0}
        end = self.task_finished if self.task_finished is not None else time.monotonic()
        paused = self.paused_seconds + (max(0,end-self.pause_started) if self.pause_started is not None else 0)
        total = max(0,end-self.task_started)
        return {"total_seconds":round(total,3),"paused_seconds":round(paused,3),
                "running_seconds":round(max(0,total-paused),3),"interventions":self.interventions}

    def task_result(self, passed, reason):
        if self.task_recorded or self.job == "monitor":
            return
        self.task_recorded = True
        result = {"task": self.job, "passed": passed, "reason": reason,
                  "time": time.time(), "elapsed": self.timing()["total_seconds"], "timing":self.timing(),
                  "stage": self.status["stage"], "order_submitted": False}
        self.update(results=(self.status.get("results", []) + [result])[-30:])
        self.event("task_result", reason, task=self.job, passed=passed, elapsed=result["elapsed"])
        if self.persist:
            atomic_json(ROOT / ("task-report-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6] + ".json"), result)

    def finish_task(self, reason):
        self.active = False
        self.task_finished = time.monotonic()
        self.update(mode="completed", reason=reason, acceptance="当前分项通过；完整结账仍需验收")
        self.task_result(True, reason)

    def local_check(self):
        self.idle_required()
        checks = []
        try:
            validate_profile(editable_profile())
            checks.append("收货／取货资料完整且格式通过")
        except ValueError as exc:
            checks.append(str(exc))
        checks.append("选购条件通过" if validate_preferences(self.preferences) else "选购条件错误")
        try:
            checks.append("Bark 已配置（尚未测试送达）" if load_payment_push_url() else "Bark 未配置")
        except Exception:
            checks.append("Bark 配置不可读取")
        checks.append("Safari 登录和官网商品尚需分别检查；本地检查不会访问 Apple")
        self.update(local_checks=checks, reason="本地检查完成；不等于官网验收通过")

    def prepare_task(self, name):
        self.idle_required()
        if time.time() < self.breaker.until:
            raise ValueError("全局冷却未结束，不能用测试按钮另发请求")
        self.job = name
        self.run_preferences = copy.deepcopy(self.preferences)
        self.start_timing()
        self.task_recorded = False
        try:
            self.push_url = load_payment_push_url()
        except Exception:
            self.push_url = ""
        self.update(job=name, acceptance="未验证", elapsed=0, next_retry=0)
        self.event("task_started", name)
        if name in PURCHASE_TASKS:
            if name == "rehearse_delivery":
                self.run_preferences["fulfillment"] = "delivery"
            elif name == "rehearse_pickup":
                self.run_preferences["fulfillment"] = "pickup"
            self.begin()
            return
        self.phase = "DIAGNOSTIC"
        self.phase_since = time.monotonic()
        self.next_probe = 0
        if name == "login":
            # Rechecking an already open account page must not initiate a new
            # sign-in navigation. Never inspect an arbitrary Safari window.
            current = self.browser.call({}, {}) if self.browser.window else {}
            if current.get("stage") != "ACCOUNT":
                self.browser.open("https://www.apple.com.cn/shop/account/home")
            else:
                self.browser.present()
            self.profile = {}
            self.phase = "PRELOGIN"
            self.prelogin_confirmations = 0
            self.active = True
            self.update(mode="running", stage="PRELOGIN", session="提前登录中；尚未确认",
                        session_checked_at=None, session_window=None,
                        reason="请在专属 Safari 窗口登录；每 2 秒只读确认，完成后自动显示结果，不加购、不刷新、不保存密码。最多等待 15 分钟。")
            return
        if name == "session_check":
            if not self.browser.window:
                raise ValueError("尚无专属窗口，请先打开账号登录")
            # Inspect the existing page; don't discard a just-completed login.
            self.active = True
        elif name == "product_check":
            self.index = 0
            self.attempt = Attempt(self.execution_preferences, variants(self.execution_preferences)[0], time.monotonic())
            self.profile = {}
            self.browser.open(CATALOG[self.execution_preferences["model"]]["url"])
            self.active = True
        self.update(mode="running", reason="检查中；不会加购或提交订单")

    def confirm_account_session(self):
        self.update(session="账号页登录已确认；后续购买复用当前窗口，结账仍可能再次认证",
                    session_checked_at=time.time(), session_window=self.browser.window)

    def prelogin_tick(self, s, now):
        """Observe login; the Edge adapter may read a second owned account page."""
        self.next_probe = time.time() + 2
        stage = s.get("stage", "UNKNOWN")
        if now - self.phase_since >= 900:
            self.attention("提前登录等待超过 15 分钟；已保留页面，可完成人工操作后继续检查")
            return
        if stage == "ACCOUNT" and s.get("authenticated_account"):
            self.prelogin_confirmations += 1
            if self.prelogin_confirmations >= 2:
                self.confirm_account_session()
                self.finish_task("提前登录完成：连续两次确认账号页已登录；没有加购，未自动开始购买")
            return
        self.prelogin_confirmations = 0
        health = s.get("diagnostics", {})
        if stage == "LOGIN_UNAVAILABLE":
            detail = ("登录回跳未完成，账号首页也未确认登录" if health.get("auth_return_not_confirmed") else
                      "域名解析失败" if health.get("auth_dns_error") else
                      "安全连接校验失败，请在 Edge 查看提示" if health.get("auth_tls_error") else
                      "登录资源被浏览器或网络拦截" if health.get("auth_client_blocked") else
                      "Apple 登录服务返回错误" if health.get("auth_http_error_count") else "登录组件网络连接失败")
            self.update(session="Apple 登录组件未就绪："+detail)
            self.attention(detail+"；请在 Edge 检查登录页面，恢复后点击“继续”。资料与购物袋保留")
            return
        if stage == "LOGIN" and health.get("auth_return_probe"):
            self.update(session="正在核对登录后的账号会话", reason="登录回跳等待较久，正在同一 Edge 会话检查账号首页；原登录页保留")
            return
        if stage == "LOGIN" and health.get("edge_browser"):
            seconds = health.get("auth_wait_seconds", 0)
            self.update(session="等待 Apple 登录完成 · %s 秒" % seconds,
                        reason="请在 Edge 完成登录；45 秒仍未确认时会检查一次账号首页，原登录页保留")
            return
        if stage == "LOADING" and health.get("edge_browser"):
            seconds = max(0, int(now-self.phase_since))
            self.update(session="Apple 登录页加载中 · 已等待 %s 秒" % seconds,
                        reason="正在等待官网登录组件；加载失败时会显示具体原因，登录完成后自动确认")
            if seconds >= 45:
                self.attention("Apple 登录页超过 45 秒仍未加载完成；请在 Edge 检查或重新加载当前登录页，恢复后点击“继续”。没有加购")
            return
        if stage in ("LOGIN", "LOADING", "ACCOUNT"):
            return
        if stage == "UNKNOWN" and now - self.phase_since < 30:
            return
        self.attention("提前登录检查暂停：" + stage + "；保留页面，不自动输入密码或验证码")

    def diagnostic_tick(self, s, now):
        stage = s.get("stage")
        if stage == "NOT_FOUND":
            self.attention("官方入口返回 404；不重复打开失效入口")
            return
        if self.job == "session_check":
            if s.get("authenticated_account"):
                self.confirm_account_session()
                self.finish_task("账号页显示退出登录入口；不代表结账免认证")
            elif stage in ("FULFILLMENT", "CONTACT", "PAYMENT", "REVIEW"):
                self.update(session="当前位于结账；无法仅据页面证明账号登录")
                self.attention("当前结账页不构成账号登录证明；未将游客会话标为已登录")
            elif now - self.phase_since >= 10:
                self.update(session="未能确认账号登录")
                self.attention("未找到可靠的已登录标记，请在账号页完成登录后再检查")
            return
        if stage == "WAIT_RELEASE":
            self.attention("入口可访问但尚未开放购买；规格与价格未验证")
            return
        decision = self.attempt.decide(s, now)
        if decision.kind == "click" and decision.reason in ("add", "product_continue"):
            entry = {"target": self.attempt.target, "price": s.get("product_price"),
                     "checked_at": time.time(), "entry": CATALOG[self.execution_preferences["model"]]["url"],
                     "scope": "仅第一优先规格；未加购、不保证实时库存"}
            self.preparation = entry
            self.update(preparation=entry)
            if self.persist:
                atomic_json(PREPARATION, entry)
            self.finish_task("第一优先规格及页面价格已核验；没有点击加购")
            self.attempt = None
        elif decision.kind == "click" and decision.reason in ("color", "capacity", "不折抵换购", "不加 AppleCare+ 服务计划", "applecare_add"):
            self.dispatch(s, decision)
        elif decision.kind not in ("wait", "monitor"):
            self.attention("规格检查未完成：" + decision.reason)
        elif now - self.phase_since > 30:
            self.attention("规格检查超时；未加购")

    def persist_cooldown(self):
        if self.persist:
            atomic_json(RUNTIME,{"cooldown_until":self.breaker.until,"failures":self.breaker.failures})

    def event(self, kind, reason="", **data):
        # Callers may only provide enum actions, timing, non-personal configuration.
        if isinstance(data.get("target"), dict):
            data["target"] = {k:v for k,v in data["target"].items() if k not in ("manual_region_token","delivery_region_override","pickup_region")}
        entry = {"time": time.time(), "run": self.run_id, "event": kind, "reason": reason, **data}
        with self.lock:
            self.history.append(entry)
            self.history = self.history[-200:]
            if self.persist:
                if EVENTS.exists() and EVENTS.stat().st_size>=16_000_000:
                    for number in (3,2,1):
                        source=EVENTS if number==1 else EVENTS.with_name(EVENTS.name+'.'+str(number-1))
                        if source.exists():os.replace(source,EVENTS.with_name(EVENTS.name+'.'+str(number)))
                with open(EVENTS, "a", encoding="utf-8") as f:
                    os.chmod(EVENTS, 0o600)
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def update(self, **values):
        with self.lock:
            self.status.update(values)

    def snapshot(self):
        with self.lock:
            s = copy.deepcopy(self.status)
            s["history"] = copy.deepcopy(self.history[-40:])
            s["preferences"] = copy.deepcopy(self.preferences)
            s["cooldown_remaining"] = max(0, round(self.breaker.until - time.time(), 1))
            s["task_owns_browser"] = self.active or self.attempt is not None or self.status["mode"] == "ready"
            s["timing"] = self.timing()
            s["login_watch"] = self.login_watch
            s["prelogin_watch"] = self.active and self.phase == "PRELOGIN"
            s["inventory"] = copy.deepcopy(self.inventory[-40:])
            return s

    def observe_diagnostics(self, snapshot):
        from payment_options import official_options
        choices=official_options(snapshot)
        if snapshot.get('stage') in ('PAYMENT','REVIEW'):
            self.update(official_payment_options={**choices,'observed_at':time.time()})
        # Persist only fixed counters/booleans, never DOM text, URLs or values.
        allowed = {"privacy_consent_required":bool,"contact_verification_required":bool,"payment_heading":bool,"payment_choice_count":int,
            "pickup_method_count":int,"pickup_method_disabled_count":int,"pickup_method_selected":bool,
            "delivery_method_count":int,"delivery_method_selected":bool,
            "target_store_count":int,"target_store_selected":bool,"pickup_date_count":int,"pickup_time_count":int,
            "bag_care_evidence_count":int,"bag_target_row_count":int,
            "product_care_count":int,"product_care_disabled_count":int,"product_care_paid_count":int,
            "visible_control_count":int,"disabled_control_count":int,
            "unknown_required_count":int,"invalid_field_count":int,
            "visible_dialog":bool,"visible_iframe_count":int,
            "edge_browser":bool,"auth_network_error_count":int,"auth_http_error_count":int,
            "auth_submission_seen":bool,"login_spinner":bool,"auth_return_probe":bool,"auth_wait_seconds":int,
            "auth_return_recovered":bool,"auth_return_not_confirmed":bool,
            "auth_blocked_count":int,"browser_read_failure_count":int,
            "auth_dns_error":bool,"auth_tls_error":bool,"auth_client_blocked":bool,
            "edge_navigation_pending":bool,"edge_presentation_failed":bool,
            "edge_navigation_retries":int,
            "access_http_status":int,"access_document":bool,"access_shop_request":bool,
            "document_http_status":int}
        raw = snapshot.get("diagnostics", {})
        safe = {k:raw[k] for k,t in allowed.items() if type(raw.get(k)) is t}
        self.update(diagnostics=safe)

    def watch_login_tick(self):
        """Observe the owned window only; no reload, navigation or login action."""
        if not self.login_watch:
            return
        now = time.monotonic()
        if now < self.login_probe_at or time.time() < self.breaker.until:
            return
        if now >= self.login_watch_until:
            self.attention("登录等待超过 15 分钟；保留现场，请人工处理后继续")
            return
        self.login_probe_at = now + 2
        s = self.browser.call(self.profile, self.attempt.target if self.attempt else {})
        self.observe_diagnostics(s)
        stage = s.get("stage", "UNKNOWN")
        self.update(stage=stage, last_check=time.time())
        if s.get("http_status") in (403,429,541) or stage == "BLOCKED":
            until = self.breaker.reject(time.time(), retry_after_seconds(str(s.get("retry_after", "")),time.time()))
            self.persist_cooldown()
            self.attention("登录后访问被拒；统一冷却，不自动重试登录")
            self.update(next_retry=until)
            return
        if stage in ("LOGIN", "LOADING") or s.get("contact_verification_required") or s.get("diagnostics", {}).get("privacy_consent_required"):
            self.login_candidate = None
            self.login_candidate_count = 0
            return
        bag_return = self.phase == "CLEAR_BAG" and self.pending_remove is None and stage == "BAG"
        checkout_return = (self.phase == "RUN" and stage in ("FULFILLMENT","CONTACT","PAYMENT","REVIEW")
                           and self.attempt and self.attempt.bag_verified)
        if not (bag_return or checkout_return):
            self.attention("登录后未回到已核对的结账流程：" + stage + "；未重新加购或跳过检查")
            return
        if s.get("diagnostics", {}).get("visible_dialog") and s.get("stock_issue") not in ("all", "delivery", "pickup"):
            self.attention("登录后出现弹窗；请核对后手动继续")
            return
        candidate = (stage, s.get("fingerprint"))
        self.login_candidate_count = self.login_candidate_count + 1 if candidate == self.login_candidate else 1
        self.login_candidate = candidate
        if self.login_candidate_count >= 2:
            self.command("continue")
            self.event("login_resumed", "连续两次确认返回原结账流程；保留购物袋与操作记录")
            self.update(session="已返回结账流程；不据此承诺免再次认证")

    def begin_region_handoff(self, reason):
        """Keep the exact attempt/cart. Only observe while the user edits."""
        self.active = False
        self.login_watch = False
        self.region_watch = True
        self.region_probe_at = 0
        self.region_candidate = None
        self.attempt.target["manual_region_token"] = uuid.uuid4().hex
        self.attempt.pending = None
        self.pause_timing()
        self.update(mode="manual_region", reason=reason, next_retry=0)
        self.event("manual_region", "等待本人选择官网地区；保留尝试及购物袋，不再自动点击地区")
        self.notify("Apple 地址需要手动选择", reason)

    def watch_region_tick(self):
        if not self.region_watch or not self.attempt:
            return
        now, wall = time.monotonic(), time.time()
        if now < self.region_probe_at or wall < self.breaker.until:
            return
        self.region_probe_at = now + .5
        s = self.browser.call(self.profile, self.attempt.target)
        self.observe_diagnostics(s)
        stage = s.get("stage", "UNKNOWN")
        self.update(stage=stage, last_check=wall, elapsed=self.attempt.elapsed(now))
        if s.get("http_status") in (403,429,541) or stage == "BLOCKED":
            until = self.breaker.reject(wall,retry_after_seconds(str(s.get("retry_after","")),wall))
            self.persist_cooldown()
            self.region_candidate = None
            self.update(reason="访问被拒，冷却期间保留手动地区任务；不会刷新或提交",next_retry=until)
            return
        if stage in ("LOGIN","CAPTCHA","FOREIGN","LOST_TAB","SESSION_EXPIRED","ORDER_PLACED"):
            self.attention("手动地区处理中需要检查："+stage+"；保留现场，未继续操作")
            return
        r = s.get("region_picker") or {}
        values = r.get("manual_values") or {}
        valid = (stage in ("FULFILLMENT","CONTACT") and r.get("context")==self.attempt.method
                 and r.get("manual_token")==self.attempt.target.get("manual_region_token")
                 and r.get("manual_ready") and r.get("reason") != "ambiguous"
                 and (r.get("manual_touched") or r.get("matched"))
                 and all(isinstance(values.get(k),str) and 0<len(values[k])<=80 for k in ("province","city","district"))
                 and not s.get("diagnostics",{}).get("visible_dialog"))
        if not valid:
            self.region_candidate = None
            return
        candidate = (stage, r["context"], tuple(values[k] for k in ("province","city","district")))
        if candidate != self.region_candidate:
            self.region_candidate, self.region_candidate_at = candidate, now
            return
        if now-self.region_candidate_at < 1:
            return
        key = "pickup_region" if self.attempt.method=="pickup" else "delivery_region_override"
        self.attempt.target[key] = {k:values[k] for k in ("province","city","district")}
        # Override is scoped to this attempt; never write private saved profile.
        self.region_watch = False
        self.attempt.target.pop("manual_region_token", None)
        self.command("continue")
        self.event("manual_region_resumed", "官网地区已稳定回读，继续原任务；未修改保存资料或重新加购")
        self.update(reason="已确认你手选的官网地区，继续当前结账流程")

    def save(self, data):
        p = validate_preferences(data)
        with self.lock:
            # Notification settings have their own live endpoint. A stale
            # purchase form must never undo the user's mute choice.
            p["bark_enabled"] = self.preferences.get("bark_enabled", True)
            if self.active or self.attempt is not None:
                raise ValueError("请先停止任务再修改设置；不会中途改变正在结账的型号")
            if self.persist:
                atomic_json(CONFIG, p)
            self.preferences = p
            self.preparation = {}
            self.update(preparation={})
            if self.persist:
                atomic_json(PREPARATION, {})
        return p

    def set_bark_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Bark 开关必须是布尔值")
        with self.lock:
            p = copy.deepcopy(self.preferences)
            p["bark_enabled"] = enabled
            if self.persist:
                atomic_json(CONFIG, p)
            self.preferences = p
            self.push_generation += 1
            self.event("notification_setting", "Bark 手机推送已开启" if enabled else "Bark 手机推送已关闭")
        return {"bark_enabled": enabled}

    def notify(self, title, body):
        with self.lock:
            if not self.preferences.get("bark_enabled", True):
                self.event("notification_muted", "Bark 已关闭，未发送：" + title)
                return
            self.pushes.put((self.push_generation, title, body))

    def push_worker(self):
        while not self.stop_event.is_set():
            try:
                generation, title, body = self.pushes.get(timeout=.5)
            except queue.Empty:
                continue
            with self.lock:
                if not self.preferences.get("bark_enabled", True) or generation != self.push_generation:
                    self.event("notification_muted", "已丢弃开关变化前的待发送提醒")
                    continue
            if not self.push_url:
                self.event("notification_unconfigured", "Bark 未配置")
                continue
            try:
                send_payment_push(self.push_url, title, body)
                self.event("notification_sent", title)
            except Exception:
                self.event("notification_failed", "推送失败；不重新执行购买操作")

    def attention(self, reason):
        self.active = False
        self.login_watch = False
        self.region_watch = False
        self.pause_timing()
        if self.attempt and self.attempt.started is not None:
            elapsed = self.attempt.elapsed(time.monotonic())
            self.update(elapsed=elapsed)
            if elapsed > 10:
                self.update(acceptance="功能尚未完成；性能待优化")
        self.update(mode="attention", reason=reason, next_retry=0)
        self.task_result(False, reason)
        self.event("attention", reason, diagnostics=self.status.get("diagnostics", {}))
        self.notify("Apple 需要处理", reason + "。请查看这台 Mac 的 Safari。")

    def begin(self):
        if not self.task_started:
            self.start_timing()
        if self.status["mode"] == "ready":
            raise ValueError("已到最终提交前，请保留页面；不要重新开始或清空购物袋")
        if self.attempt and self.attempt.add_submitted:
            raise ValueError("上次可能已经加购，请先停止并人工检查购物袋，再开始")
        self.profile = normalized_profile(require_pickup=self.execution_preferences["fulfillment"] in ("pickup", "delivery_first"))
        try:
            self.push_url = load_payment_push_url()
        except RuntimeError:
            self.push_url = ""
        self.attempt = None
        self.index = 0
        self.recoveries = 0
        self.active = True
        self.preflight()

    def rotate_entry(self, url):
        self.browser.rotate(url)

    def preflight(self, fresh=False):
        if time.time() < self.breaker.until:
            self.phase = "COOLDOWN"
            self.next_probe = self.breaker.until
            return
        self.phase = "CLEAR_BAG"
        self.next_probe = 0
        self.phase_since = time.monotonic()
        self.pending_remove = None
        if fresh:
            self.rotate_entry("https://www.apple.com.cn/shop/bag")
        else:
            self.browser.open("https://www.apple.com.cn/shop/bag")
        self.update(mode="running", stage=self.phase, reason="开始新尝试前清空购物袋；不会提交订单", elapsed=0)

    def start_product(self, fresh=False):
        target = purchase_targets(self.execution_preferences)[self.index]
        self.attempt = Attempt(self.execution_preferences, target, time.monotonic())
        self.phase = "RUN"
        self.phase_since = time.monotonic()
        url = CATALOG[self.execution_preferences["model"]]["url"]
        if fresh:
            self.rotate_entry(url)
        else:
            self.browser.navigate(url, foreground=True)
        self.last_entry_request = time.time()
        method_label = "配送到家" if target["fulfillment"] == "delivery" else self.execution_preferences["store"] + "自提"
        self.event("attempt_created", "开始新尝试：" + method_label, target=target, attempt=uuid.uuid4().hex[:10])
        self.update(target=target, elapsed=0, acceptance="未验证", mode="running", stage="LOADING",
                    next_retry=0, reason="打开选购入口，正在确认目标配置 · " + method_label)

    def schedule_product_retry(self, reason, next_variant=False, needs_cart=False):
        """Only monitor mode retries; never abandon an ambiguous cart write."""
        if self.job != "monitor":
            self.attention(reason + "；单次演练停止")
            return
        if next_variant:
            self.index = (self.index + 1) % len(purchase_targets(self.execution_preferences))
        self.retry_needs_cart = needs_cart or bool(self.attempt and
            (self.attempt.add_submitted or self.attempt.bag_verified))
        self.attempt = None
        self.phase = "RETRY_PRODUCT"
        wall = time.time()
        self.next_probe = max(wall, self.last_entry_request + self.breaker.interval(self.execution_preferences, wall))
        target = purchase_targets(self.execution_preferences)[self.index]
        method_label = "配送到家" if target["fulfillment"] == "delivery" else self.execution_preferences["store"] + "自提"
        reason += "；下一尝试：" + method_label + " / " + target["color"] + " " + target["capacity"]
        self.event("retry_scheduled", reason, target=target,
                   next_retry=self.next_probe)
        self.update(stage="RETRY_PRODUCT", reason=reason, next_retry=self.next_probe)

    def command(self, name):
        if isinstance(name, dict):
            self.idle_required()
            store_profile(name["profile"])
            self.update(reason="资料已保存到钥匙串并回读确认；没有写入日志", profile_saved_at=time.time(),profile_saved_id=name.get('request_id',''))
            return
        if name == "local_check":
            self.local_check()
            return
        if name in CONTROLS - {"start", "pause", "stop", "continue", "local_check"}:
            self.prepare_task(name)
            return
        if name == "start":
            if not self.active:
                self.idle_required()
                self.job = "monitor"
                self.start_timing()
                self.run_preferences = copy.deepcopy(self.preferences)
                self.update(job="monitor")
                self.begin()
        elif name == "pause":
            self.active = False
            self.login_watch = False
            self.region_watch = False
            self.pause_timing()
            self.update(mode="paused", reason="已暂停，保留 Safari 页面")
        elif name == "stop":
            self.login_watch = False
            self.region_watch = False
            if self.task_finished is None:
                self.task_finished = time.monotonic()
            self.task_result(False, "用户停止，未完成验收")
            self.active = False
            self.attempt = None
            self.phase = "IDLE"
            self.run_preferences = None
            self.update(mode="stopped", stage="IDLE", reason="已停止；未更改现有购物袋", next_retry=0)
        elif name == "continue":
            if self.attempt and self.attempt.target.get("manual_region_token"):
                # A premature Continue is not consent to use an old/default
                # region. Only the read-back watcher may resume this handoff.
                self.region_watch = True
                self.region_candidate = None
                self.region_probe_at = 0
                self.update(mode="manual_region", reason="请先在 Safari 完整选择省、市、区；核对完成后会自动继续")
                return
            if self.active:
                raise ValueError("任务正在运行，不需要重复继续")
            if self.status["mode"] in ("ready", "completed"):
                raise ValueError("当前任务已完成；最终提交由本人操作，新的演练请先停止后重新开始")
            if self.phase not in ("RUN", "CLEAR_BAG", "DIAGNOSTIC", "RETRY_PRODUCT", "WAIT_RELEASE", "WAIT", "COOLDOWN", "PRELOGIN"):
                raise ValueError("请使用开始备战")
            if self.attempt:
                self.attempt.target.pop("manual_region_token", None)
                # Preserve timer, target and non-idempotent action ledger.
                self.attempt.terminal = None
                self.attempt.last_progress = time.monotonic()
                self.attempt.pending = None
                self.attempt.pending_navigation = None
                self.attempt.stage_entered = None
                self.attempt.observed_stage = None
                self.attempt.stock_candidate = None
                self.attempt.region_actions = {}
                self.attempt.region_started = None
                self.attempt.region_wait = None
            self.phase_since = time.monotonic()
            if self.phase == "PRELOGIN":
                self.prelogin_confirmations = 0
                self.next_probe = 0
            if self.job != "monitor":
                self.task_recorded = False
            self.resume_timing()
            self.login_watch = False
            self.region_watch = False
            self.active = True
            self.update(mode="running", reason="从当前页面重新识别；不重复加购")
        else:
            raise ValueError("未知操作")

    def dispatch(self, snapshot, decision):
        command = asdict(decision)
        command.update(expected=snapshot["fingerprint"], nonce=uuid.uuid4().hex)
        result = self.browser.call(self.profile, self.attempt.target if self.attempt else {}, command)
        if result.get("stage") == "BLOCKED" or result.get("http_status") in (403,429,541):
            self.observe_diagnostics(result)
            wall = time.time()
            until = self.breaker.reject(wall, retry_after_seconds(str(result.get("retry_after", "")), wall))
            self.persist_cooldown()
            self.attention("操作时官网拒绝访问；结果需核对，不重复点击")
            self.update(next_retry=until)
            return
        if result.get("stage") == "LOADING":
            result = {"result":"stale"}
        rejection_labels = {"element_replaced":"页面重绘替换了原控件，重新识别",
            "access_denied":"官网拒绝访问，未执行本次操作",
            "page_not_found":"官网返回 404，未执行本次操作",
            "element_disabled":"控件临时不可用，重新识别", "control_changed":"控件状态变化，重新识别",
            "protected_action":"命中最终提交／付款／协议保护",
            "field_validation":"字段填入后校验未通过", "quantity_validation":"数量校验未通过",
            "pickup_schedule_validation":"取货日期或签到时段未通过校验，已保留现场",
            "region_changed":"地区选择状态已变化，未执行旧操作",
            "region_validation":"地区选择未通过官网校验，未使用旧地址",
            "unsupported_action":"当前控件不支持该操作"}
        rejection = rejection_labels.get(result.get("reason"), "未返回具体原因")
        self.event("action", decision.reason, result=result.get("result", "unknown"),
                   detail=rejection if result.get("result") in ("stale","refused") else "")
        if result.get("result") == "stale":
            # The adapter has proven it performed NO mutation. Do not consume a
            # retry allowance or retain an add-to-cart claim for this rejection.
            if self.attempt:
                self.attempt.sent[decision.reason] = max(0,self.attempt.sent.get(decision.reason,0)-1)
                if decision.key in self.attempt.region_actions:
                    count, _ = self.attempt.region_actions[decision.key]
                    self.attempt.region_actions[decision.key] = (max(0, count-1), -100)
                self.attempt.pending = None
                if decision.reason in ("continue_FULFILLMENT", "continue_CONTACT", "continue_PAYMENT"):
                    self.attempt.pending_navigation = None
                if decision.reason in ("add", "product_continue") and not self.attempt.sent[decision.reason]:
                    self.attempt.add_submitted = False
            if decision.kind == "remove":
                self.pending_remove = None
        if result.get("result") not in ("dispatched", "stale"):
            self.attention("本地操作检查暂停：" + rejection + "（步骤：" + decision.reason + "）")

    def tick(self):
        now, wall = time.monotonic(), time.time()
        if wall < self.breaker.until:
            self.update(mode="cooldown", stage="COOLDOWN", next_retry=self.breaker.until)
            return
        if self.phase == "COOLDOWN":
            self.attempt = None
            self.preflight()
            return
        if wall < self.next_probe:
            return
        if self.phase == "RETRY_PRODUCT":
            if self.retry_needs_cart:
                self.retry_needs_cart = False
                self.preflight(fresh=True)
            else:
                self.start_product(fresh=True)
            return
        if self.phase == "WAIT_RELEASE":
            self.phase = "RUN"
            self.phase_since = now
            self.browser.navigate(CATALOG[self.execution_preferences["model"]]["url"])
            self.last_entry_request = wall
            return
        if self.phase == "WAIT":
            self.attempt = None
            self.preflight()
            return
        target = self.attempt.target if self.attempt else {}
        s = self.browser.call(self.profile, target)
        self.observe_diagnostics(s)
        now = time.monotonic()
        if self.attempt and self.attempt.started is not None:
            self.update(elapsed=self.attempt.elapsed(now))
        self.update(stage=s.get("stage", "UNKNOWN"), last_check=wall)
        if s.get("http_status") in (403,429,541) or s.get("stage") == "BLOCKED":
            until = self.breaker.reject(wall, retry_after_seconds(str(s.get("retry_after", "")), wall))
            self.persist_cooldown()
            if self.job != "monitor" or (self.attempt and self.attempt.add_submitted):
                self.attention("Apple 拒绝访问；购物袋可能已变化，需人工检查后继续")
                self.update(next_retry=until)
            else:
                self.phase = "COOLDOWN"
                self.update(mode="cooldown", reason="Apple 拒绝访问；全部检测统一冷却", next_retry=until)
                self.event("blocked", "访问被拒，未绕过限制", next_retry=until)
            return
        if self.phase == "PRELOGIN":
            self.prelogin_tick(s, now)
            return
        if s.get("stage") in ("FOREIGN", "LOST_TAB", "LOGIN", "CAPTCHA", "SESSION_EXPIRED"):
            if s.get("stage") == "LOGIN":
                self.update(session="Apple 要求重新认证；历史提前登录不代表本次结账已认证",
                            session_checked_at=None, session_window=None)
            self.attention("需要人工处理：" + s["stage"])
            can_watch = ((self.phase == "RUN" and self.attempt and self.attempt.bag_verified) or
                         (self.phase == "CLEAR_BAG" and self.pending_remove is None))
            if s.get("stage") == "LOGIN" and can_watch:
                self.login_watch = True
                self.login_watch_until = time.monotonic() + 900
                self.login_probe_at = 0
                self.login_candidate = None
                self.login_candidate_count = 0
                self.update(reason="请本人登录；后台每 2 秒只读检查，回到原结账页后自动继续。暂停／停止可取消自动恢复")
            return
        if self.phase == "DIAGNOSTIC":
            self.diagnostic_tick(s, now)
            return
        if self.job in PURCHASE_TASKS and self.timing()["running_seconds"] > 300:
            self.attention("单次演练超过 5 分钟，已保留现场")
            return
        if self.phase == "CLEAR_BAG":
            if (self.job == "monitor" and self.pending_remove is None and
                    (s.get("stage") == "STORE_WAIT" or
                     (s.get("stage") == "LOADING" and now - self.phase_since > 30))):
                self.last_entry_request = wall  # Do not hot-loop a closed store.
                self.schedule_product_retry("官网暂未就绪；稍后重新检查购物袋", needs_cart=True)
                return
            self.clear_tick(s, now)
            return
        if (self.job == "monitor" and self.attempt and not self.attempt.add_submitted and
                (s.get("stage") == "STORE_WAIT" or
                 (s.get("stage") == "LOADING" and now - self.phase_since > 30))):
            self.schedule_product_retry("官网暂未就绪；等待后切换专属窗口重新打开入口")
            return
        if not self.attempt:
            self.attention("尝试上下文缺失")
            return
        old_stage = self.status.get("last_progress_stage")
        if old_stage != s.get("stage"):
            self.event("stage", s.get("stage", "UNKNOWN"), elapsed=self.attempt.elapsed(now))
            self.update(last_progress_stage=s.get("stage"))
        previous_start = self.attempt.started
        decision = self.attempt.decide(s, now)
        elapsed = self.attempt.elapsed(now)
        self.update(elapsed=elapsed, reason=decision.reason)
        if previous_start is None and self.attempt.started is not None:
            self.event("purchasable", "验收计时开始", target=target)
            self.notify("Apple 发现可买", target["name"] + " " + target["capacity"] + " " + target["color"])
        if elapsed > 10 and not self.attempt.slow_recorded:
            self.attempt.slow_recorded = True
            self.event("performance_pending", "超过 10 秒，性能待优化；继续功能验收", elapsed=elapsed)
            self.update(acceptance="功能验收进行中；性能待优化")
        kind = decision.kind
        if self.job == "bag_test" and kind == "click" and decision.reason == "checkout":
            self.finish_task(f"加购测试通过：已核对目标、数量 {self.attempt.target.get('quantity', 1)} 和预算；未点击结账")
            return
        if kind in ("click", "fill", "set"):
            self.dispatch(s, decision)
        elif kind == "manual_region":
            self.begin_region_handoff(decision.reason)
        elif kind == "ready":
            self.active = False
            self.task_finished = time.monotonic()
            passed = elapsed <= 10 and self.attempt.started is not None
            self.update(mode="ready", stage="READY", acceptance="本次功能通过；≤10秒" if passed else "本次功能通过；性能待优化")
            self.event("ready", decision.reason, elapsed=elapsed, within_10s=passed, target=target, method=self.attempt.method)
            self.task_result(True, "已到最终提交前；没有下单或付款")
            self.notify("Apple 已到最终提交前", "尚未下单或付款。请回到这台 Mac 的 Safari 核对并提交。")
        elif kind in ("attention", "inspect_bag"):
            if kind == "inspect_bag":
                self.browser.navigate("https://www.apple.com.cn/shop/bag")
            self.attention(decision.reason)
        elif kind == "canonical":
            if self.recoveries >= 1:
                self.attention("官方入口仍然 404，已暂停")
            else:
                self.recoveries += 1
                self.attempt.terminal = None
                self.browser.navigate(CATALOG[self.execution_preferences["model"]]["url"])
        elif kind in ("sold_out", "over_budget"):
            self.event(kind, decision.reason, elapsed=elapsed, target=target)
            self.inventory.append({"time":wall,"target":target,"state":kind})
            self.inventory = self.inventory[-40:]
            if self.job in PURCHASE_TASKS:
                self.attention(decision.reason + "；单次测试结束，不循环抢购")
                return
            self.schedule_product_retry(decision.reason + "；按优先级切换下一配置", next_variant=True)
        elif kind == "monitor":
            if self.job in PURCHASE_TASKS:
                self.attention("尚未开放购买，演练未验证；不会无限等待")
                return
            self.breaker.success()
            self.persist_cooldown()
            self.last_entry_request = wall
            self.schedule_product_retry("官网尚未开放购买；持续等待并轮换专属入口窗口")
        elif now - self.phase_since > 30 and self.attempt.started is None:
            self.attention("页面加载或识别超过 30 秒；未开始购买计时")

    def clear_tick(self, s, now):
        if s.get("stage") == "NOT_FOUND":
            if self.pending_remove:
                self.attention("移除购物袋商品后官网返回 404；移除结果未确认，已停止，不会重复移除或加购")
            else:
                self.attention("购物袋入口被重定向至 404；未执行移除或加购，需核对官网入口")
            return
        if now - self.phase_since > 30:
            self.attention("购物袋移除结果超过 30 秒仍未确认；保留现场，不重复移除或加购" if self.pending_remove else
                           "购物袋页面超过 30 秒仍未完成识别；未启动购买")
            return
        if s.get("stage") != "BAG":
            return
        if s.get("empty_bag") and not s.get("items"):
            self.event("bag_cleared", "已确认购物袋为空")
            self.start_product()
            return
        items = s.get("items", [])
        if not items:
            return  # Unknown markup is not proof of an empty bag.
        fingerprint = s["fingerprint"]
        if self.pending_remove:
            old_count, sent, checked = self.pending_remove
            if len(items) >= old_count:
                if now - sent > 5 and not checked:
                    # A delayed response is not proof that deletion failed.
                    # Re-read server cart once; NEVER repeat the remove click.
                    self.pending_remove = (old_count, sent, True)
                    self.event("bag_recheck", "移除响应延迟，重新读取购物袋核实；不重复移除")
                    self.browser.navigate("https://www.apple.com.cn/shop/bag")
                return
        item = items[0]
        if not item.get("remove_ref"):
            self.attention("购物袋移除控件无法识别")
            return
        from checkout_engine import Decision
        self.pending_remove = (len(items), now, False)
        self.dispatch(s, Decision("remove", "清空购物袋：移除一项（可重新加购）", item["remove_ref"]))

    def run(self):
        while not self.stop_event.is_set():
            try:
                try:
                    name = self.commands.get_nowait()
                except queue.Empty:
                    name = None
                if name:
                    try:
                        self.update(command_error="")
                        self.command(name)
                    except ValueError as exc:
                        # Reject a conflicting UI command without interrupting
                        # the task currently owning the browser.
                        self.event("command_rejected", str(exc))
                        self.update(command_error=str(exc))
                if self.active:
                    self.tick()
                elif self.login_watch:
                    self.watch_login_tick()
                elif self.region_watch:
                    self.watch_region_tick()
            except Exception as exc:
                # Only our fixed ValueError / RuntimeError messages are safe.
                message = str(exc) if type(exc) in (ValueError, RuntimeError) else "内部错误，已安全暂停；请检查程序"
                self.attention(message)
            self.stop_event.wait(.05 if self.active and self.phase == "RUN" else .4)


def handler(service, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def local(self):
            return self.headers.get("Host") == f"127.0.0.1:{port}"

        def do_GET(self):
            if not self.local():
                return self.send_json(403, {"error": "仅限本机控制台"})
            if self.path == "/api/status":
                return self.send_json(200, service.snapshot())
            if self.path == "/api/catalog":
                return self.send_json(200, CATALOG)
            if self.path in ("/", "/dashboard.js"):
                path = ROOT / ("checkout_dashboard.html" if self.path == "/" else "checkout_dashboard.js")
                body = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8" if self.path == "/" else "text/javascript; charset=utf-8")
                self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return self.send_json(404, {"error": "不存在"})

        def do_POST(self):
            if (not self.local() or self.headers.get("Origin") != f"http://127.0.0.1:{port}" or
                    self.headers.get("Content-Type", "").split(";")[0] != "application/json"):
                return self.send_json(403, {"error": "禁止跨站控制"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16384:
                    raise ValueError("请求大小错误")
                data = json.loads(self.rfile.read(size))
                if self.path == "/api/preferences":
                    return self.send_json(200, service.save(data))
                if self.path == "/api/notifications":
                    return self.send_json(200, service.set_bark_enabled(data.get("bark_enabled")))
                if self.path == "/api/profile/load":
                    return self.send_json(200, editable_profile())
                if self.path == "/api/profile/save":
                    service.idle_required()
                    if service.commands.qsize() >= 10:
                        return self.send_json(429, {"error":"待处理操作过多"})
                    request_id=data.pop('_request_id','')
                    if not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,64}',request_id):
                        raise ValueError('保存请求标识无效，请刷新界面后重试')
                    profile = validate_profile(data)
                    service.commands.put({"profile":profile,'request_id':request_id})
                    return self.send_json(202, {"queued":"profile_save",'request_id':request_id})
                if self.path == "/api/control" and data.get("action") in getattr(service, "controls", CONTROLS):
                    if service.commands.qsize() >= 10:
                        return self.send_json(429, {"error":"待处理操作过多，请等当前操作完成"})
                    service.commands.put(data["action"])
                    return self.send_json(202, {"queued": data["action"]})
                raise ValueError("未知操作")
            except (ValueError, TypeError, AttributeError) as exc:
                return self.send_json(400, {"error": str(exc) if type(exc) is ValueError else "设置格式错误"})
    return Handler


def main():
    parser = argparse.ArgumentParser(description="Apple Safari 备战控制台（启动后默认停止）")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--probe", choices=list(CATALOG), help="只读公开商品页；不会加购或读取个人资料")
    parser.add_argument("--rehearsal", choices=["delivery","pickup"], help="真实演练一次 iPhone 17 白色 256GB；清空购物袋，禁止最终提交")
    parser.add_argument("--rehearsal-window", type=int, default=0, help="仅复用本程序已有演练报告记录的独立窗口")
    args = parser.parse_args()
    if args.rehearsal:
        rehearsal_lock = open(LOCK, "a")
        try:
            fcntl.flock(rehearsal_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("已有控制台／演练运行，请先停止它，禁止同时执行两个流程")
            return 2
        p = load_preferences()
        p.update(model="iphone-17",colors=["白色"],capacities=["256GB"],release_at="",fulfillment=args.rehearsal)
        s = Service(preferences=validate_preferences(p))
        if args.rehearsal_window:
            known = set()
            for report_file in ROOT.glob("rehearsal-*.json"):
                try:
                    known.add(json.loads(report_file.read_text()).get("safari_window"))
                except (ValueError,OSError):
                    pass
            if args.rehearsal_window not in known:
                print("不是本程序记录的测试窗口，拒绝接管")
                return 2
            s.browser.window = args.rehearsal_window
        started = time.monotonic()
        try:
            s.begin()
            while s.active and time.monotonic()-started < 300:
                s.tick()
                time.sleep(.05)
            if s.active:
                s.attention("本次功能演练超过 5 分钟，保留现场")
        except Exception as exc:
            s.attention(str(exc) if type(exc) in (RuntimeError,ValueError) else "演练异常，已暂停")
        passed = s.status["mode"] == "ready"
        report = {"time":time.time(), "mode":args.rehearsal,"passed":passed,
                  "acceptance_criterion":"functional", "within_10s":passed and s.status["elapsed"] <= 10,
                  "stage":s.status["stage"],"reason":s.status["reason"],"elapsed":s.status["elapsed"],
                  "wall_elapsed":round(time.monotonic()-started,3),
                  "events":[e for e in s.history if e.get("run")==s.run_id],
                  "safari_window":s.browser.window,
                  "order_submitted":False}
        report_path = ROOT / ("rehearsal-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
        atomic_json(report_path,report)
        print(json.dumps({**{k:v for k,v in report.items() if k!="events"},"report":str(report_path)},ensure_ascii=False))
        return 0 if passed else 2
    if args.probe:
        b = Safari()
        b.open(CATALOG[args.probe]["url"])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            s = b.call({}, {})
            if s.get("stage") not in ("LOADING", "UNKNOWN"):
                print(json.dumps({k: s.get(k) for k in ("stage","http_status","purchasable")}, ensure_ascii=False))
                return 0
            time.sleep(.5)
        print("页面未能识别，不能视为验证成功")
        return 2
    with open(LOCK, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("控制台已经运行")
            return 2
        service = Service()
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(service, args.port))
        for target in (service.run, service.push_worker):
            threading.Thread(target=target, daemon=True).start()
        def shutdown(*_):
            service.stop_event.set()
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        print(f"控制台已就绪 http://127.0.0.1:{args.port}/（未开始购买）", flush=True)
        try:
            server.serve_forever(poll_interval=.25)
        finally:
            service.stop_event.set()
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
