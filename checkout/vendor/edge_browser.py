"""Owned Edge pages over loopback CDP. No personal browser attachment."""
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from urllib.parse import urlparse
import urllib.request
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


def find_edge():
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            executable = Path(root) / "Microsoft/Edge/Application/msedge.exe"
            if executable.is_file():
                return executable
    raise RuntimeError("未找到 Microsoft Edge；请先安装或修复 Edge，再重新检查浏览器")


class EdgeSession:
    """One driver, one worker thread, shared account for checkout and observer."""
    def __init__(self, directory, driver_factory=None, executable=None):
        self.directory = Path(directory) / "edge-profile"
        self.driver_factory = driver_factory
        self.executable = executable
        self.driver = self.browser = self.context = None
        self.worker = None
        self.failed = False
        self.process = None

    def assert_worker(self):
        current = threading.get_ident()
        if self.worker is None:
            self.worker = current
        elif self.worker != current:
            raise RuntimeError("浏览器操作必须在同一任务线程执行")

    def endpoint(self):
        try:
            lines = (self.directory / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()
            port = int(lines[0])
            browser_path = lines[1]
            if not 1 <= port <= 65535 or not re.fullmatch(r"/devtools/browser/[a-zA-Z0-9-]+", browser_path):
                return None
            base = f"http://127.0.0.1:{port}"
            # No proxies, redirects, or endpoint supplied by the website.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(base + "/json/version", timeout=.5) as response:
                if response.geturl() != base + "/json/version":
                    return None
                info = json.load(response)
            remote = urlparse(info.get("webSocketDebuggerUrl", ""))
            if remote.scheme != "ws" or remote.hostname != "127.0.0.1" or remote.port != port or remote.path != browser_path:
                return None
            return "ws://127.0.0.1:" + str(port) + browser_path
        except (OSError, ValueError, IndexError, TypeError):
            return None

    def ensure(self):
        self.assert_worker()
        if self.failed:
            raise RuntimeError("Edge 连接已丢失；请停止任务并重新启动助手，原页面保留")
        if self.browser:
            if not self.browser.is_connected():
                self.failed = True
                raise RuntimeError("Edge 连接已丢失；已保留现场，不会重新加购")
            return self.context
        try:
            executable = self.executable or find_edge()
            self.directory.mkdir(parents=True, exist_ok=True)
            endpoint = self.endpoint()
            if not endpoint:
                # Launch outside Playwright so disconnecting the driver leaves
                # Edge and the user's review/payment page alive.
                process = subprocess.Popen([str(executable), "--remote-debugging-port=0",
                    "--remote-debugging-address=127.0.0.1", "--user-data-dir=" + str(self.directory),
                    "--no-first-run", "--no-default-browser-check", "about:blank"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.process = process
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    endpoint = self.endpoint()
                    if endpoint:
                        break
                    if process.poll() not in (None, 0):
                        break
                    time.sleep(.1)
            if not endpoint:
                raise RuntimeError("Edge 未通过本机连接检查；请关闭专属 Edge 后重试，或检查组织策略")
            if self.driver_factory is None:
                from playwright.sync_api import sync_playwright
                self.driver_factory = sync_playwright
            self.driver = self.driver_factory().start()
            self.browser = self.driver.chromium.connect_over_cdp(endpoint, timeout=4000)
            self.context = self.browser.contexts[0]
            self.context.set_default_timeout(4000)
            self.context.set_default_navigation_timeout(4000)
            return self.context
        except RuntimeError:
            self.disconnect()
            raise
        except Exception:
            self.disconnect()
            raise RuntimeError("Edge 自动化连接失败；请确认程序完整、Edge 可用且未被组织策略禁止") from None

    def disconnect(self):
        self.assert_worker()
        # Never browser.close(), context.close(), or terminate Edge. Stopping
        # the CDP client does not own the externally launched browser process.
        if self.driver:
            try:
                self.driver.stop()
            except Exception:
                pass
        self.driver = self.browser = self.context = None


class EdgeBrowser:
    # Fixed by the adapter class, never taken from website/IPC input.
    entrypoint = 'appleCheckout'
    def __init__(self, session, script):
        self.session, self.js = session, script
        self.window = 0
        self.windows = []
        self.pages = {}
        self.channels = {}
        self.dialogs = {}
        self.expected_path = ""
        self.account_entry = False
        self.last_status = {}
        self.auth_health = {}
        self.read_failures = {}
        self.auth_submitted = {}
        self.login_since = None
        self.login_probe = None
        self.login_probe_started = 0
        self.login_probe_done = False
        self.navigation_pending = False
        self.presentation_failed = False
        self.shop_denials = {}
        self.navigation_retries = 0
        self.product_requests = {}
        self.product_stability = {}
        self.denial_components = {}
        self.document_generations = {}
        self.request_generations = {}
        self.request_sequence = 0
        self.auxiliary_health = {}
        self.auxiliary_versions = {}
        self.auxiliary_wait = {}

    @staticmethod
    def validate_url(url):
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.netloc != "www.apple.com.cn" or
                not parsed.path.startswith("/shop/") or parsed.query or parsed.fragment):
            raise ValueError("只允许已知 Apple 中国商店入口")
        return parsed.path

    def page(self):
        self.session.ensure()
        page = self.pages.get(self.window)
        if page is None or page.is_closed():
            raise RuntimeError("专属 Edge 页面已关闭；已停止，不接管其他页面")
        return page

    def prepare_entry(self):
        """Called by an idle service for an explicit new task, never polling."""
        self.session.assert_worker()
        if self.session.failed or (self.session.browser and not self.session.browser.is_connected()):
            self.session.disconnect()
            self.session.failed = False
            # Existing Edge tabs are preserved. A new task gets its own page;
            # neither an old checkout nor any personal page will be adopted.
            self.window = 0
            self.windows.clear()
            self.pages.clear()
            self.channels.clear()
            self.dialogs.clear()
            self.last_status.clear()
            self.shop_denials.clear()
            self.denial_components.clear()
            self.product_requests.clear()
            self.product_stability.clear()
            self.document_generations.clear()
            self.request_generations.clear()
            self.auxiliary_health.clear()
            self.auxiliary_versions.clear()
            self.auxiliary_wait.clear()
            self.auth_health.clear()
            self.read_failures.clear()
            self.auth_submitted.clear()
        elif self.window:
            page = self.pages.get(self.window)
            if page is None or page.is_closed():
                identity = self.window
                self.window = 0
                self.windows = [value for value in self.windows if value != identity]
                for mapping in (self.pages,self.channels,self.dialogs,self.last_status,self.shop_denials,self.denial_components,self.product_requests,self.product_stability,self.auth_health,self.read_failures,self.auth_submitted,self.document_generations,self.auxiliary_health,self.auxiliary_versions,self.auxiliary_wait):
                    mapping.pop(identity,None)
                self.request_generations = {r:m for r,m in self.request_generations.items() if m[0] != identity}
        self.expected_path = ""
        self.login_since = None
        self.login_probe = None
        self.login_probe_done = False
        self.navigation_pending = False
        self.presentation_failed = False
        self.navigation_retries = 0

    def new_page(self):
        page = self.session.ensure().new_page()
        return self.bind_page(page)

    def bind_page(self, page):
        """Attach the same guarded observers to a caller-owned native page."""
        identity = uuid.uuid4().hex
        self.pages[identity] = page
        channel = self.session.context.new_cdp_session(page)
        self.channels[identity] = channel
        channel.on("Page.javascriptDialogClosed", lambda _: self.dialogs.pop(identity, None))
        channel.send("Page.enable")
        self.window = identity
        self.document_generations[identity] = 0
        page.on("response", lambda response: self.response(identity, page, response))
        page.on("request", lambda request: self.request_started(identity, page, request))
        page.on("requestfinished", lambda request: self.request_finished(identity, request))
        page.on("requestfailed", lambda request: self.request_failed(identity, request))
        # Leave native dialogs visible for the user, rather than Playwright's
        # default automatic dismissal. Never read their potentially private text.
        page.on("dialog", lambda dialog: self.dialogs.__setitem__(identity, True))
        return page

    def response(self, identity, page, response):
        if not self.current_request(identity, response.request):
            return
        # Keep only main-document status and Retry-After, never raw URLs/PII.
        if response.request.is_navigation_request() and response.frame == page.main_frame:
            self.last_status[identity] = (response.status, response.headers.get("retry-after", ""))
            self.shop_denials.pop(identity, None)
            self.denial_components.pop(identity, None)
            self.product_requests.pop(identity, None)
            self.product_stability.pop(identity, None)
            self.auxiliary_health.pop(identity, None)
            self.auxiliary_versions.pop(identity, None)
            # Retry-After is a deadline, not a document error. Navigation must
            # not erase a server-requested waiting period.
        elif self.shop_resource(response.request) and self.auxiliary_resource(response.request):
            self.observe_auxiliary(identity, response)
        elif self.shop_resource(response.request) and response.status in (403, 429, 541):
            # Add-to-bag may fail through fetch without replacing the document.
            # Keep that ambiguous write visible; never retry it automatically.
            self.shop_denials[identity] = (response.status, response.headers.get("retry-after", ""))
            self.denial_components[identity] = self.shop_component(response.request)
        key = self.auth_resource(response.request)
        if key:
            if response.request.method == "POST" and 200 <= response.status < 300:
                self.auth_submitted[identity] = True
            # These endpoints include normal 401/409/challenge responses; do
            # not interpret authentication protocol steps as access denial.
            blocked = response.status in (429, 541) or (response.status == 403 and response.request.method in ("GET", "HEAD"))
            if blocked or response.status >= 500:
                rows = self.auth_health.setdefault(identity, {})
                if len(rows) < 32 or key in rows:
                    rows[key] = (response.status, response.headers.get("retry-after", ""), "")
            elif 200 <= response.status < 400:
                self.auth_health.get(identity, {}).pop(key, None)

    def request_started(self, identity, page, request):
        if request.is_navigation_request() and request.frame == page.main_frame:
            self.document_generations[identity] = self.document_generations.get(identity, 0) + 1
            self.product_requests.pop(identity, None)
            self.product_stability.pop(identity, None)
        self.request_sequence += 1
        self.request_generations[request] = (
            identity, self.document_generations.get(identity, 0), self.request_sequence)
        self.product_request(identity, request)

    def current_request(self, identity, request):
        metadata = self.request_generations.get(request)
        # Legacy injected unit fixtures have no event subscription. Real owned
        # pages always initialise a generation and reject untracked responses.
        if metadata is None:
            return identity not in self.document_generations
        return metadata[:2] == (identity, self.document_generations.get(identity, 0))

    def request_finished(self, identity, request):
        if self.current_request(identity, request):
            self.product_request_done(identity, request)
        self.request_generations.pop(request, None)

    @staticmethod
    def auxiliary_resource(request):
        # Positive read-only allowlist. Unknown endpoints, configuration
        # updates and all mutations still fail closed.
        if request.method not in ("GET", "HEAD"):
            return False
        path = urlparse(request.url).path.lower().rstrip("/")
        return path in ("/shop/fulfillment-messages", "/shop/retail/pickup-message",
                        "/shop/favoritesx/fetch", "/shop/api/product-gallery")

    def observe_auxiliary(self, identity, response):
        from checkout_engine import retry_after_seconds
        request = response.request
        # A digest distinguishes product queries without retaining session
        # parameters or exposing them to diagnostics.
        key = hashlib.sha256((request.method + " " + request.url).encode()).hexdigest()
        metadata = self.request_generations.get(request)
        sequence = metadata[2] if metadata else self.request_sequence + 1
        versions = self.auxiliary_versions.setdefault(identity, {})
        if sequence < versions.get(key, -1):
            return
        versions[key] = sequence
        rows = self.auxiliary_health.setdefault(identity, {})
        if response.status in (403, 429, 541) or response.status >= 500:
            rows[key] = (self.shop_component(request), response.status)
            delay = retry_after_seconds(response.headers.get("retry-after", ""), time.time())
            if delay > 0:
                self.auxiliary_wait[identity] = max(
                    self.auxiliary_wait.get(identity, 0), time.monotonic() + delay)
        elif 200 <= response.status < 300:
            rows.pop(key, None)

    def auxiliary_diagnostics(self):
        rows = list(self.auxiliary_health.get(self.window, {}).values())
        return {"edge_auxiliary_error_count": len(rows),
                "edge_availability_unknown": any(kind == "availability" for kind, _ in rows),
                "edge_auxiliary_http_status": rows[-1][1] if rows else 0}

    def auxiliary_cooldown(self):
        remaining = self.auxiliary_wait.get(self.window, 0) - time.monotonic()
        if remaining <= 0:
            return None
        return {"stage": "LOADING", "read_wait_seconds": remaining,
                "diagnostics": dict(self.auxiliary_diagnostics(), edge_auxiliary_wait=True)}

    @staticmethod
    def shop_component(request):
        """Fixed diagnostic categories only. Never retain URLs or query values."""
        path = urlparse(request.url).path.lower()
        if path.startswith(("/shop/fulfillment-messages", "/shop/retail/pickup-message")):
            return "availability"
        if path.startswith("/shop/bag"):
            return "bag"
        if path.startswith("/shop/checkout"):
            return "checkout"
        if path.startswith(("/shop/updatesummary", "/shop/api/applecare", "/shop/api/product-gallery")):
            return "configuration"
        return "other"

    def product_request(self, identity, request):
        if self.shop_resource(request) and self.shop_component(request) in ("availability", "configuration"):
            self.product_requests.setdefault(identity, set()).add(id(request))
            self.product_stability.pop(identity, None)

    def product_request_done(self, identity, request):
        pending = self.product_requests.get(identity, set())
        if id(request) in pending:
            pending.discard(id(request))
            self.product_stability.pop(identity, None)

    @staticmethod
    def shop_resource(request):
        try:
            parsed = urlparse(request.url)
            return (parsed.scheme == "https" and
                    re.fullmatch(r"(?:www\.|secure\d*\.www\.|secure\.store\.|store\.)?apple\.com\.cn",
                                 parsed.hostname or "") is not None and
                    parsed.path.startswith("/shop/") and
                    request.resource_type in ("xhr", "fetch"))
        except Exception:
            return False

    def access_failure(self):
        status, retry = self.last_status.get(self.window, (0, ""))
        source = "document"
        if status not in (403, 429, 541):
            status, retry = self.shop_denials.get(self.window, (0, ""))
            source = "shop_request"
        if status in (403, 429, 541):
            return {"stage": "BLOCKED", "http_status": status, "retry_after": retry,
                    "diagnostics": {"edge_browser": True, "access_http_status": status,
                                    "access_document": source == "document",
                                    "access_shop_request": source == "shop_request",
                                    "access_availability": source == "shop_request" and self.denial_components.get(self.window) == "availability",
                                    "access_configuration": source == "shop_request" and self.denial_components.get(self.window) == "configuration",
                                    "access_bag": source == "shop_request" and self.denial_components.get(self.window) == "bag"}}
        return None

    @staticmethod
    def auth_resource(request):
        """Allowlisted component identity only, never body, cookies or headers."""
        try:
            parsed = urlparse(request.url)
            host = parsed.hostname or ""
            if parsed.scheme != "https" or request.resource_type not in ("document", "script", "xhr", "fetch"):
                return None
            auth = host in ("idmsa.apple.com", "appleid.apple.com", "account.apple.com")
            auth = auth or (host == "appleid.cdn-apple.com" and "/appleauth/" in parsed.path)
            auth = auth or (re.fullmatch(r"(?:www\.|secure\d*\.www\.|secure\.store\.|store\.)?apple\.com\.cn",host) and
                            re.match(r"/shop/(?:signIn|account)(?:/|$)",parsed.path,re.I))
            return (host, parsed.path, request.resource_type) if auth else None
        except Exception:
            return None

    def request_failed(self, identity, request):
        if not self.current_request(identity, request):
            self.request_generations.pop(request, None)
            return
        self.product_request_done(identity, request)
        self.request_generations.pop(request, None)
        key = self.auth_resource(request)
        if not key:
            return
        # Aborts are expected when a frame redirects. Persist only fixed enums.
        raw = request.failure or ""
        known = ("ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION_TIMED_OUT", "ERR_CONNECTION_RESET", "ERR_CONNECTION_CLOSED",
                 "ERR_INTERNET_DISCONNECTED", "ERR_BLOCKED_BY_CLIENT", "ERR_BLOCKED_BY_RESPONSE",
                 "ERR_CERT_AUTHORITY_INVALID", "ERR_CERT_DATE_INVALID", "ERR_SSL_PROTOCOL_ERROR", "ERR_TUNNEL_CONNECTION_FAILED")
        code = next((code for code in known if code in raw), "")
        if code:
            rows = self.auth_health.setdefault(identity, {})
            if len(rows) < 32 or key in rows:
                rows[key] = (0, "", code)

    def login_health(self):
        rows = list(self.auth_health.get(self.window, {}).values())
        blocked = [(status,retry) for status,retry,_ in rows if status in (403,429,541)]
        diagnostics = {"edge_browser":True,"auth_network_error_count":sum(bool(error) for _,_,error in rows),
                       "auth_submission_seen":bool(self.auth_submitted.get(self.window)),
                       "auth_http_error_count":sum(status >= 500 for status,_,_ in rows),
                       "auth_blocked_count":len(blocked),
                       "auth_dns_error":any(error == "ERR_NAME_NOT_RESOLVED" for _,_,error in rows),
                       "auth_tls_error":any(error.startswith(("ERR_CERT_", "ERR_SSL_")) for _,_,error in rows),
                       "auth_client_blocked":any(error in ("ERR_BLOCKED_BY_CLIENT","ERR_BLOCKED_BY_RESPONSE") for _,_,error in rows),
                       "browser_read_failure_count":self.read_failures.get(self.window,0)}
        if blocked:
            # Select the longest Retry-After, including HTTP dates, not simply
            # whichever failing subframe happened to finish last.
            from checkout_engine import retry_after_seconds
            status,retry = max(blocked,key=lambda row:retry_after_seconds(row[1],time.time()))
            return {"stage":"BLOCKED","http_status":status,"retry_after":retry,"diagnostics":diagnostics}
        if any(diagnostics[k] for k in ("auth_network_error_count","auth_http_error_count")):
            return {"stage":"LOGIN_UNAVAILABLE","diagnostics":diagnostics}
        return {"diagnostics":diagnostics}

    def open(self, url, foreground=True):
        self.validate_url(url)
        if not self.window:
            self.new_page()
        if url == "https://www.apple.com.cn/shop/bag" and self.call({}, {}).get("stage") == "BAG":
            if foreground:
                self.present()
            return
        self.navigate(url, foreground)

    def reopen_failed_entry(self, url):
        """Replace only a confirmed failed owned tab with a safe bag read."""
        self.validate_url(url)
        failed = self.window
        page = self.page()
        if self.call({}, {}).get('stage') != 'NOT_FOUND':
            return False
        self.new_page()
        self.navigate(url, foreground=True)
        page.close()
        self.windows = [identity for identity in self.windows if identity != failed]
        for mapping in (self.pages, self.channels, self.dialogs, self.last_status,
                        self.shop_denials, self.denial_components, self.product_requests,
                        self.product_stability, self.document_generations, self.auth_health,
                        self.read_failures, self.auth_submitted, self.auxiliary_health,
                        self.auxiliary_versions, self.auxiliary_wait):
            mapping.pop(failed, None)
        self.request_generations = {request: mark for request, mark in self.request_generations.items()
                                    if mark[0] != failed}
        return True

    def navigate(self, url, foreground=False):
        path = self.validate_url(url)
        self.account_entry = bool(re.match(r"/shop/account(?:/|$)", path))
        page = self.page()
        self.expected_path = path
        self.last_status.pop(self.window, None)
        self.shop_denials.pop(self.window, None)
        self.denial_components.pop(self.window, None)
        self.product_requests.pop(self.window, None)
        self.product_stability.pop(self.window, None)
        self.auxiliary_health.pop(self.window, None)
        self.auxiliary_versions.pop(self.window, None)
        # Invalidate outstanding responses even when goto fails before commit.
        self.document_generations[self.window] = self.document_generations.get(self.window, 0) + 1
        self.auth_health.pop(self.window, None)
        self.read_failures.pop(self.window, None)
        self.auth_submitted.pop(self.window, None)
        self.login_since = None
        self.login_probe = None
        self.login_probe_done = False
        self.navigation_pending = False
        self.presentation_failed = False
        self.navigation_retries = 0
        safe_get = path == "/shop/bag" or re.fullmatch(r"/shop/buy-iphone/[a-z0-9-]+", path)
        for navigation_attempt in range(2):
            try:
                page.goto(url, wait_until="commit", timeout=4000)
                break
            except Exception as error:
                # Only a failed safe GET is retried. Never replay a DOM click,
                # POST, login, checkout or unknown add-to-bag operation.
                code_match = re.search(r"\bERR_[A-Z0-9_]+\b", str(error))
                code = code_match.group(0) if code_match else ""
                if code == "ERR_NETWORK_CHANGED" and safe_get and navigation_attempt == 0:
                    self.navigation_retries = 1
                    page.wait_for_timeout(500)
                    self.page()  # Re-check cancellation/ownership after waiting.
                    continue
                timeout = (isinstance(error, TimeoutError) or
                           (type(error).__name__ == 'TimeoutError' and
                            type(error).__module__.startswith('playwright.')))
                if timeout:
                    self.navigation_pending = True
                    break
                explanation = {
                    "ERR_NETWORK_CHANGED": ("网络连接反复切换；一次恢复未成功" if self.navigation_retries
                                            else "网络连接发生切换"),
                    "ERR_NAME_NOT_RESOLVED": "域名解析失败",
                    "ERR_PROXY_CONNECTION_FAILED": "代理连接失败",
                    "ERR_TUNNEL_CONNECTION_FAILED": "代理通道建立失败",
                    "ERR_INTERNET_DISCONNECTED": "网络已断开",
                    "ERR_CONNECTION_RESET": "连接被重置",
                }.get(code, "Edge 导航失败")
                suffix = "（" + code + "）" if code else ""
                raise RuntimeError(explanation + suffix + "；已保留页面，未重复加购，请处理网络后继续") from None
        if foreground:
            # Losing foreground focus is not losing our owned page. A window
            # presentation failure must not stop otherwise valid selection.
            self.page()  # Missing/closed owned target remains a hard failure.
            try:
                self.present()
            except RuntimeError:
                self.page()
                self.presentation_failed = True

    def present(self):
        try:
            page = self.page()
            info = self.channels[self.window].send("Browser.getWindowForTarget")
            if info.get("bounds",{}).get("windowState") == "minimized":
                self.channels[self.window].send("Browser.setWindowBounds",{
                    "windowId":info["windowId"],"bounds":{"windowState":"normal"}})
            page.bring_to_front()
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError("无法显示专属 Edge 页面；请在浏览器查看") from None

    def rotate(self, url):
        self.validate_url(url)
        if self.window and self.window not in self.windows:
            self.windows.append(self.window)
        if len(self.windows) < 2:
            self.new_page()
            self.windows.append(self.window)
        else:
            self.window = next(identity for identity in self.windows if identity != self.window)
        self.navigate(url, foreground=True)

    def check(self):
        try:
            self.page()
            return self.evaluate("String(1 + 1)") == "2"
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError("Edge 脚本检查失败，请重新检查浏览器连接") from None

    def evaluate(self, expression):
        # Page.evaluate has no timeout parameter. Bound synchronous execution
        # at the browser protocol boundary (including runaway page scripts).
        result = self.channels[self.window].send("Runtime.evaluate", {
            "expression":expression, "returnByValue":True, "timeout":4000})
        if result.get("exceptionDetails"):
            raise RuntimeError("页面脚本未完成；已保留现场")
        return result.get("result", {}).get("value")

    def inspect_login_return(self):
        """One read-only account probe in this exact profile; preserve sign-in.

        Do not reuse an SSI URL, replay credentials, adopt a personal tab, or
        infer success merely because the sign-in request returned HTTP 200.
        """
        if self.login_probe is None:
            if self.login_probe_done:
                return None
            self.login_probe_done = True
            self.login_probe_started = time.monotonic()
            probe = EdgeBrowser(self.session, self.js)
            # Event handlers on either owned page update the same ledgers.
            for name in ("pages","channels","dialogs","last_status","shop_denials","auth_health","read_failures","auth_submitted",
                         "denial_components","product_requests","product_stability","document_generations",
                         "request_generations","auxiliary_health","auxiliary_versions","auxiliary_wait"):
                setattr(probe,name,getattr(self,name))
            self.login_probe = probe
            try:
                probe.open("https://www.apple.com.cn/shop/account/home",foreground=False)
            except RuntimeError:
                # A commit may still arrive. Observe this owned page on the
                # next tick rather than issuing another navigation.
                pass
            probe.login_probe_done = True  # A probe never creates a probe.
        probe = self.login_probe
        try:
            result = probe.call({}, {})
        except RuntimeError:
            return {"stage":"LOGIN_UNAVAILABLE","diagnostics":{"edge_browser":True,"auth_return_not_confirmed":True}}
        if result.get("stage") == "ACCOUNT" and result.get("authenticated_account"):
            self.window = probe.window
            self.expected_path = ""
            self.login_probe = None
            result.setdefault("diagnostics",{})["auth_return_recovered"] = True
            return result
        if result.get("stage") in ("BLOCKED","CAPTCHA","LOGIN_UNAVAILABLE"):
            return result
        if result.get("stage") in ("NOT_FOUND","FOREIGN","LOST_TAB"):
            return {"stage":"LOGIN_UNAVAILABLE","diagnostics":{"edge_browser":True,"auth_return_not_confirmed":True}}
        if time.monotonic()-self.login_probe_started >= 30:
            return {"stage":"LOGIN_UNAVAILABLE","diagnostics":{"edge_browser":True,"auth_return_not_confirmed":True}}
        return None

    def call(self, profile, target, command=None):
        page = self.page()
        payload = {"profile": profile, "target": target, "expected_path": self.expected_path}
        if command:
            payload["command"] = command
        try:
            self.channels[self.window].send("Page.getLayoutMetrics")
            denial = self.access_failure()
            if denial:
                # Check before injecting a command, not after a possible click.
                return dict(denial, result="refused", reason="access_denied") if command else denial
            waiting = self.auxiliary_cooldown()
            if waiting:
                return dict(waiting, result="stale", reason="product_updating") if command else waiting
            if self.last_status.get(self.window, (0, ""))[0] == 404:
                result = {"stage": "NOT_FOUND", "http_status": 404,
                          "diagnostics": {"edge_browser": True, "document_http_status": 404}}
                return dict(result, result="refused", reason="page_not_found") if command else result
            if self.dialogs.get(self.window):
                # Read-only query pumps events, including manual dialog closure.
                return {"stage":"UNKNOWN","diagnostics":{"visible_dialog":True}}
            # Safari's Apple-event round trips used to mask this race. Edge
            # returns at HTTP commit; an SSR form can be clickable before its
            # scripts attach handlers. Never click that intermediate document.
            product_page = isinstance(page.url, str) and "/shop/buy-iphone/" in urlparse(page.url).path
            if product_page:
                ready = self.evaluate("document.readyState")
                pending = len(self.product_requests.get(self.window, ()))
                if ready != "complete" or pending:
                    self.product_stability.pop(self.window, None)
                    waiting = {"stage": "LOADING", "diagnostics": {
                        "edge_product_loading": ready != "complete",
                        "edge_product_pending": pending}}
                    return dict(waiting, result="stale", reason="product_updating") if command else waiting
                if command:
                    stable, since = self.product_stability.get(self.window, (None, time.monotonic()))
                    if stable is None or stable != command.get("expected") or time.monotonic() - since < .35:
                        return {"stage": "LOADING", "result": "stale", "reason": "product_updating",
                                "diagnostics": {"edge_product_settling": True}}
            # The function registry stays on the document. Never persist a
            # snapshot, request payload, exception source, or storage_state.
            result = self.evaluate("(() => {" + self.js + "\nreturn " + self.entrypoint + "(" +
                                   json.dumps(payload, ensure_ascii=False) + ");})()")
        except Exception:
            if command:
                raise RuntimeError("Edge 操作结果未确认；已保留现场，不重复操作") from None
            if page.is_closed():
                return {"stage": "LOST_TAB"}
            if not self.session.browser.is_connected():
                self.session.failed = True
                raise RuntimeError("Edge 连接已丢失；已保留现场") from None
            self.read_failures[self.window] = self.read_failures.get(self.window,0)+1
            result = self.login_health()
            result.setdefault("stage", "UNKNOWN" if self.read_failures[self.window] >= 3 else "LOADING")
            status, retry = self.last_status.get(self.window,(0,""))
            if status in (403,429,541):result.update(stage="BLOCKED",http_status=status,retry_after=retry)
            return result
        if not isinstance(result, dict):
            if command:
                raise RuntimeError("Edge 操作结果未确认；已保留现场，不重复操作")
            self.read_failures[self.window] = self.read_failures.get(self.window,0)+1
            health = self.login_health()
            health.setdefault("stage", "UNKNOWN" if self.read_failures[self.window] >= 3 else "LOADING")
            status, retry = self.last_status.get(self.window,(0,""))
            if status in (403,429,541):health.update(stage="BLOCKED",http_status=status,retry_after=retry)
            return health
        self.read_failures[self.window] = 0
        if (product_page and not command and result.get("fingerprint")
                and result.get("stage") not in ("LOADING", "UNKNOWN")):
            # The official post-add accessory/attach screen can retain a
            # /buy-iphone/ URL. Stabilize its own fingerprint too; otherwise
            # every "view bag" click is forever rejected as product_updating.
            fingerprint = result.get("fingerprint")
            previous, since = self.product_stability.get(self.window, (None, time.monotonic()))
            if fingerprint != previous:
                since = time.monotonic()
                self.product_stability[self.window] = (fingerprint, since)
            # Readback must survive a short render/response settling window.
            # No random delays, stealth settings, or retries of failed writes.
            if time.monotonic() - since < .35:
                return {"stage": "LOADING", "diagnostics": {"edge_product_settling": True}}
        if product_page and command and result.get("result") == "dispatched":
            self.product_stability.pop(self.window, None)
        health = self.login_health()
        result.setdefault("diagnostics",{}).update(health["diagnostics"])
        result['diagnostics'].update(edge_navigation_pending=self.navigation_pending,
                                     edge_presentation_failed=self.presentation_failed,
                                     edge_navigation_retries=self.navigation_retries)
        # A completed, authenticated account page is stronger than a stale
        # component error. Never claim login success based on network alone.
        if result.get("authenticated_account"):
            self.auth_health.pop(self.window,None)
        elif result.get("stage") in ("LOGIN","ACCOUNT","LOADING","UNKNOWN") and "stage" in health:
            result.update({k:v for k,v in health.items() if k != "diagnostics"})
        if not command and result.get("stage") == "LOGIN":
            if self.login_since is None:
                self.login_since = time.monotonic()
            d=result.get("diagnostics",{})
            waiting_return = d.get("login_spinner") or d.get("auth_submission_seen")
            # Apple can render the spinner inside a cross-origin frame, or
            # change its CSS, without emitting a recognizable auth response.
            # Always bound the wait; the fallback only reads a second owned
            # account page and never interrupts the form being edited.
            elapsed = time.monotonic()-self.login_since
            d["auth_wait_seconds"] = max(0, int(elapsed))
            if self.account_entry and (self.login_probe is not None or elapsed >= (30 if waiting_return else 45)):
                recovered=self.inspect_login_return()
                if recovered:
                    result=recovered
                else:
                    if self.login_probe is not None:
                        result.setdefault("diagnostics",{})["auth_return_probe"] = True
        elif result.get("stage") == "ACCOUNT":
            self.login_since = None
        status, retry_after = self.last_status.get(self.window, (0, ""))
        denial = self.access_failure()
        if denial:
            return denial
        waiting = self.auxiliary_cooldown()
        if waiting and not command:
            return waiting
        if status == 404:
            result.update(stage="NOT_FOUND", http_status=404)
        result.setdefault("diagnostics", {})["document_http_status"] = status
        result["diagnostics"].update(self.auxiliary_diagnostics())
        if result.get("stage") not in (None, "LOADING"):
            self.expected_path = ""
            self.navigation_pending = False
        return result
