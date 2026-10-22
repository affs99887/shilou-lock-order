import assert from "node:assert/strict";
import test from "node:test";
import { loadSource } from "./load-source.mjs";

function renderApp() {
  const saved = [];
  const addedTargets = [];
  const purchaseRequests = [];
  const purchase = { disabled: false, pending: false, confirmationTarget: null, error: "",
    purchase: target => purchaseRequests.push(target), confirm() {}, cancel() {} };
  const pushSaves = [];
  const state = {
    settings: { locale: "zh_CN", barkUrl: "https://api.day.app/saved-key", intervalSeconds: 30 },
    stores: [], products: [], rows: [], regions: [], categories: [], logs: [],
    category: "iphone", trouble: null, update: null,
  };
  const hooks = [];
  let cursor = 0;
  // 宿主仅替代 React hooks 与外观组件；执行 App 自身的 JSX 和输入事件处理器。
  const App = loadSource("src/App.tsx", (specifier) => {
    if (specifier === "@/lib/inventory-purchase") return { useInventoryPurchase: () => purchase };
    if (specifier === "react") return {
      useEffect() {},
      useRef: () => ({ current: null }),
      useMemo: (compute) => compute(),
      useSyncExternalStore: (_subscribe, getSnapshot) => getSnapshot(),
      useState(initial) {
        const index = cursor++;
        if (!(index in hooks)) hooks[index] = initial;
        return [hooks[index], (value) => { hooks[index] = value; }];
      },
    };
    if (specifier === "@/lib/store") return {
      watcherStore: { getSnapshot: () => state },
      saveSettings: async (settings) => { saved.push(settings); state.settings = settings; },
      setTargets: async (targets) => { addedTargets.push(targets); },
      setCategory: (category) => { state.category = category; },
      changeLocale: async (locale) => { state.settings = { ...state.settings, locale }; },
      setPushUrls: async (urls) => {
        pushSaves.push(urls);
        // 模拟后端分栏存放。真正的归类规则在 Rust 侧，由 crates/apw-core/tests/config.rs 覆盖；
        // 这里只需要让界面拿到「哪个地址存在哪一栏」。
        const feishu = urls.filter((url) => url.includes("/open-apis/bot/v2/hook/"));
        const bark = urls.filter((url) => !feishu.includes(url));
        state.settings = { ...state.settings, barkUrl: bark.join(";"), feishuWebhook: feishu.join(";") };
      },
    };
    if (specifier.startsWith("@/components/") || specifier === "lucide-react") {
      return new Proxy({}, { get: (_target, name) => String(name) });
    }
  }).default;
  function control(predicate) {
    cursor = 0;
    const find = (element) => {
      if (!element || typeof element !== "object") return;
      if (predicate(element)) return element;
      const children = Array.isArray(element) ? element : element?.props?.children;
      for (const child of Array.isArray(children) ? children : [children]) {
        const result = find(child);
        if (result) return result;
      }
    };
    const element = find(App());
    assert.ok(element, "expected UI control to be rendered");
    return element.props;
  }
  function all(predicate) {
    cursor = 0;
    const found = [];
    const walk = (element) => {
      if (!element || typeof element !== "object") return;
      if (predicate(element)) found.push(element);
      const children = Array.isArray(element) ? element : element?.props?.children;
      for (const child of Array.isArray(children) ? children : [children]) walk(child);
    };
    walk(App());
    return found;
  }
  const isPushInput = (element) => /^push-\d+$/.test(element?.props?.id ?? "");
  const isAddPush = (element) => element.type === "Button" &&
    Array.isArray(element.props.children) && element.props.children.includes(" 添加推送地址");
  return {
    proxyRow: (index) => control((element) => element?.props?.id === `proxy-${index}`),
    proxyValues: () => all((element) => /^proxy-\d+$/.test(element?.props?.id ?? "")).map((element) => element.props.value),
    removeProxy: (index) => control((element) => element?.props?.["aria-label"] === `删除代理地址 ${index + 1}`),
    rejectedProxies: () => all((element) => element.type === "Badge" && element.props.children === "未生效").length,
    addProxy: () => control((element) => element.type === "Button" &&
      Array.isArray(element.props.children) && element.props.children.includes(" 添加代理")),
    pushRow: (index) => control((element) => element?.props?.id === `push-${index}`),
    pushValues: () => all(isPushInput).map((element) => element.props.value),
    // 渠道标签按行的顺序排列；还没保存的行没有标签。
    pushLabels: () => all((element) => element.type === "Badge" && element.props.children !== "未生效")
      .map((element) => element.props.children),
    removePush: (index) => control((element) => element?.props?.["aria-label"] === `删除推送地址 ${index + 1}`),
    addPush: () => control(isAddPush),
    hasAddPush: () => all(isAddPush).length > 0,
    choice: (placeholder) => control((element) => element?.props?.placeholder === placeholder),
    addButton: () => control((element) => element.type === "Button" &&
      Array.isArray(element.props.children) && element.props.children.includes(" 添加")),
    scaleHint: () => control((element) => element.type === "span" &&
      Array.isArray(element.props.children) && element.props.children.includes(" 个型号 × ")),
    state, saved, addedTargets, pushSaves, purchase, purchaseRequests,
    control, all,
  };
}

test("only in-stock rows offer manual purchase and disable it while checkout is engaged", () => {
  const app=renderApp();
  const target={locale:"zh_CN",storeNumber:"R683",storeTitle:"环球港",partNumber:"TEST",productName:"测试商品"};
  app.state.rows=[{target,availability:{kind:"in_stock"}}];
  const button=()=>app.control(e=>e.type==="Button" && e.props.children==="立即选购");
  assert.equal(button().disabled,false);button().onClick();
  assert.deepEqual(app.purchaseRequests,[target]);
  app.purchase.disabled=true;assert.equal(button().disabled,true);
  app.state.rows[0].availability={kind:"out_of_stock"};
  assert.equal(app.all(e=>e.type==="Button" && e.props.children==="立即选购").length,0);
});

test("blocked monitoring exposes the error and cooldown without pretending to query", () => {
  const app = renderApp();
  const detail = "HTTP 541；直连已进入冷却，5 分钟后自动重试一次";
  app.state.trouble = { reason: `查询失败：${detail}`, advice: "try_another_network" };
  app.state.logs = ["[12:30:00] 告警：查询失败：HTTP 541"];
  app.state.rows = [{
    target: { locale: "zh_CN", storeNumber: "R575", storeTitle: "Apple 武汉",
      partNumber: "TEST", productName: "演练型号 256GB 白色" },
    availability: { kind: "unknown", reason: "blocked", detail },
    lastCheckedMs: 1729200000000,
  }];
  assert.equal(app.control(e => e.props?.["data-testid"] === "monitor-protection").role, "status");
  assert.equal(app.all(e => e.props?.children === "库存查询受阻").length, 1);
  assert.equal(app.all(e => e.props?.children === app.state.trouble.reason).length, 1);
  assert.equal(app.all(e => e.props?.line === app.state.logs[0]).length, 1);
  const badge = app.all(e => typeof e.type === "function" && e.type.name === "StatusBadge")[0];
  const rendered = badge.type(badge.props);
  assert.equal(rendered.props.children[1].props.children, `请求被 Apple 拦截：${detail}`);
  assert.equal(rendered.props.children[0].props.children.props.children.props.children, "未知");
  assert.equal(app.control(e => e.props?.label === "查询异常").value, 1);
  assert.equal(app.state.rows[0].availability.kind, "unknown");
  app.state.trouble = null;
  assert.equal(app.all(e => e.props?.["data-testid"] === "monitor-protection").length, 0);
});

test("pending and failed inventory remain distinguishable with their actual reasons", () => {
  const app = renderApp();
  const cases = [
    [{ reason: "not_yet_checked" }, "尚未轮到这一项"],
    [{ reason: "blocked", detail: "HTTP 403" }, "请求被 Apple 拦截：HTTP 403"],
    [{ reason: "rate_limited" }, "Apple 接口限流或暂时不可用，程序会延长间隔后重试。具体原因见查询日志。"],
    [{ reason: "transport", detail: "连接超时" }, "网络请求失败：连接超时"],
    [{ reason: "schema_drift", field: "pickupDisplay", raw: "changed" }, "接口返回结构与预期不符：pickupDisplay = changed"],
    [{ reason: "schema_drift", field: "pickupDisplay", raw: "default" },
      "Apple 暂未开放这款在该店的到店取货（官网状态 default），开放后会自动显示有货或无货"],
    [{ reason: "apple_error", message: "服务不可用" }, "Apple 返回错误：服务不可用"],
  ];
  for (const [reason, detail] of cases) {
    app.state.rows = [{
      target: { locale: "zh_CN", storeNumber: "R575", storeTitle: "Apple 武汉",
        partNumber: "TEST", productName: "演练型号" },
      availability: { kind: "unknown", ...reason }, lastCheckedMs: null,
    }];
    const node = app.all(e => typeof e.type === "function" && e.type.name === "StatusBadge")[0];
    const rendered = node.type(node.props);
    const badge = rendered.props.children[0].props.children.props.children;
    const pending = reason.reason === "not_yet_checked";
    assert.equal(badge.props.children, pending ? "待查询" : "未知");
    assert.equal(badge.props.className.includes("border-dashed"), pending);
    assert.equal(badge.props.className.includes("text-unknown"), !pending);
    assert.equal(rendered.props.children[1].props.children, detail);
    assert.equal(app.all(e => e.props?.label === "查询异常").length, pending ? 0 : 1);
    assert.equal(app.state.rows[0].availability.kind, "unknown");
  }
});

test("budget pacing is identified separately from query failures", () => {
  const app = renderApp();
  app.state.running = true;
  app.state.pacing = { paced: true, nextCheckInSecs: 137 };
  assert.equal(app.all(e => e.type === "span" && e.props.children === "查询预算等待").length, 1);
  const children = app.all(e => e.props?.children !== undefined)
    .flatMap(e => [e.props.children].flat()).filter(v => typeof v === "string" || typeof v === "number");
  assert.ok(!children.includes(137));
  assert.ok(!children.some(v => typeof v === "string" && /下一轮|扫描频率/.test(v)));
  assert.equal(app.all(e => e.type === "TableHead" && e.props.children === "最后检查").length, 0);
});

test("inventory omits check times while query logs keep their timestamps and records", () => {
  const app = renderApp();
  app.state.rows = [{
    target: { locale: "zh_CN", storeNumber: "R575", storeTitle: "Apple 武汉",
      partNumber: "TEST", productName: "演练型号 256GB 白色" },
    availability: { kind: "in_stock" }, lastCheckedMs: 1729200000000,
  }];
  app.state.logs = ["[12:30:00] 查询完成"];
  const layout = app.control(e => e.props?.["data-testid"] === "watcher-layout");
  assert.ok(layout.className.split(" ").includes("min-h-screen"));
  assert.ok(!layout.className.split(" ").includes("h-screen"));
  const table = app.control(e => e.props?.["aria-label"] === "监控型号与库存状态");
  assert.ok(table.className.includes("shrink-0"));
  assert.ok(!table.className.includes("min-h-0"));
  assert.equal(app.all(e => e.type === "TableHead" && e.props.children === "最后检查").length, 0);
  assert.equal(app.all(e => e.type === "TableCell" && e.props.children === "演练型号 256GB 白色").length, 1);
  assert.equal(app.all(e => e.type === "TableCell").length, 4);
  assert.ok(app.control(e => e.props?.["aria-label"] === "查询日志").className.includes("shrink-0"));
  assert.equal(app.all(e => e.props?.line === "[12:30:00] 查询完成").length, 1);
  assert.equal(app.all(e => e.type === "AutoCheckout").length, 1);
});

test("folding secondary settings preserves drafts and keeps targets beside inventory", () => {
  const app = renderApp();
  const settings = () => app.control(e => e.props?.["aria-controls"] === "notification-settings");
  const checkout = () => app.control(e => e.type === "AutoCheckout");
  assert.notEqual(app.control(e => e.props?.["data-testid"] === "inventory-page").hidden, true);
  assert.notEqual(app.control(e => e.props?.["data-testid"] === "target-page").hidden, true);
  app.choice("选择自提门店").onChange(["R575"]);
  settings().onClick();
  assert.equal(app.control(e => e.props?.id === "notification-settings").hidden, false);
  settings().onClick();
  assert.equal(app.control(e => e.props?.id === "notification-settings").hidden, true);
  assert.deepEqual(app.choice("选择自提门店").values, ["R575"]);
  checkout().onSettings();
  assert.equal(checkout().expanded, true);
  checkout().onSettings();
  assert.equal(checkout().expanded, false);
  assert.equal(app.all(e => e.type === "AutoCheckout").length, 1);
  assert.equal(app.all(e => e.type === "BarkSettings").length, 1);
  assert.equal(app.all(e => e.type === "ProxySettings").length, 0);
  assert.equal(app.all(e => e.type === "MonitorSources").length, 0);
  assert.deepEqual(app.saved, []);
  assert.deepEqual(app.addedTargets, []);
});

test("monitoring uses the original cooldown guidance even with saved legacy proxies", () => {
  const app = renderApp();
  const settingsPage = app.control(e => e.props?.["data-testid"] === "settings-page");
  assert.ok(![settingsPage.children].flat().some(e => e?.type === "ProxySettings"));
  const hint = () => app.all(e => e.type === "p" && typeof e.props.children === "string" &&
    e.props.children.startsWith("受影响的库存状态为未知。"))[0].props.children;
  app.state.trouble = { reason: "查询失败：HTTP 541", advice: "try_another_network" };
  assert.match(hint(), /等待冷却结束/);
  app.state.settings = { ...app.state.settings, proxies: ["http://127.0.0.1:18001"], proxyMode: "pool" };
  assert.match(hint(), /等待冷却结束/);
  app.state.settings = { ...app.state.settings, proxies: ["http://user:pass@tunnel.example:15818"], proxyMode: "tunnel" };
  assert.match(hint(), /等待冷却结束/);
  app.state.settings = { ...app.state.settings, proxies: [], proxyMode: "tunnel" };
  assert.match(hint(), /等待冷却结束/, "没有代理时模式不起作用");
});

const K1 = "https://api.day.app/k1";
const K2 = "https://api.day.app/k2";
const F1 = "https://open.feishu.cn/open-apis/bot/v2/hook/f1";





function phoneSelectionApp() {
  const app = renderApp();
  app.state.stores = [{ number: "R532", title: "杭州万象城" }];
  app.state.products = [
    { partNumber: "A", category: "iphone", family: "iphone18pro", capacity: "256GB", color: "黑色", title: "iPhone 18 Pro 256GB 黑色" },
    { partNumber: "B", category: "iphone", family: "iphone18pro", capacity: "512GB", color: "银色", title: "iPhone 18 Pro 512GB 银色" },
    { partNumber: "C", category: "iphone", family: "iphone18promax", capacity: "512GB", color: "银色", title: "iPhone 18 Pro Max 512GB 银色" },
    { partNumber: "D", category: "ipad", family: "ipadpro", capacity: "256GB", color: "银色", title: "iPad Pro 256GB 银色" },
  ];
  app.choice("选择自提门店").onChange(["R532"]);
  app.choice("选择机型").onChange(["iphone18pro"]);
  app.choice("选择容量").onChange(["512GB"]);
  app.choice("选择颜色").onChange(["银色"]);
  return app;
}


test("iPhone choices add the exact monitoring target and reset after adding", async () => {
  const app = phoneSelectionApp();
  assert.equal(app.addButton().disabled, false);
  app.addButton().onClick();
  await new Promise(setImmediate);
  assert.deepEqual(app.addedTargets, [[{
    locale: "zh_CN", storeNumber: "R532", storeTitle: "杭州万象城",
    partNumber: "B", productName: "iPhone 18 Pro 512GB 银色",
  }]]);
  assert.deepEqual(app.choice("选择机型").values, []);
  assert.equal(app.choice("选择容量").disabled, true);
  assert.equal(app.choice("选择颜色").disabled, true);
  assert.equal(app.addButton().disabled, true);
});

test("narrowing storage or model keeps the choices that still exist and drops the rest", () => {
  const app = phoneSelectionApp();
  // 只留 256GB：Pro 的 256GB 只有黑色，之前选的银色不再成立，被去掉。
  app.choice("选择容量").onChange(["256GB"]);
  assert.deepEqual(app.choice("选择颜色").options, [{ value: "黑色", label: "黑色" }]);
  assert.deepEqual(app.choice("选择颜色").values, []);
  assert.equal(app.addButton().disabled, true);
  app.choice("选择颜色").onChange(["黑色"]);
  assert.equal(app.addButton().disabled, false);
  // 换成 Pro Max：它没有 256GB，容量和颜色都跟着清空。
  app.choice("选择机型").onChange(["iphone18promax"]);
  assert.deepEqual(app.choice("选择容量").values, []);
  assert.deepEqual(app.choice("选择颜色").values, []);
  assert.equal(app.addButton().disabled, true);
});

test("switching model keeps a storage and colour the new model also has", () => {
  const app = phoneSelectionApp();
  // 从 Pro 换到 Pro Max，512GB 银色两款都有，不必重选。
  app.choice("选择机型").onChange(["iphone18promax"]);
  assert.deepEqual(app.choice("选择容量").values, ["512GB"]);
  assert.deepEqual(app.choice("选择颜色").values, ["银色"]);
  assert.equal(app.addButton().disabled, false);
});

test("several models, storages and colours add every existing combination for every store", async () => {
  const app = phoneSelectionApp();
  app.state.stores = [
    { number: "R532", title: "杭州万象城" },
    { number: "R471", title: "杭州西湖" },
  ];
  app.choice("选择自提门店").onChange(["R532", "R471"]);
  app.choice("选择机型").onChange(["iphone18pro", "iphone18promax"]);
  app.choice("选择容量").onChange(["256GB", "512GB"]);
  app.choice("选择颜色").onChange(["黑色", "银色"]);
  // 八种组合里目录只有三台，提示里点明规模。
  assert.equal(app.scaleHint().children.join(""), "3 个型号 × 2 家门店");
  assert.equal(app.addButton().disabled, false);
  app.addButton().onClick();
  await new Promise(setImmediate);
  assert.deepEqual(app.addedTargets, [[
    { locale: "zh_CN", storeNumber: "R532", storeTitle: "杭州万象城", partNumber: "A", productName: "iPhone 18 Pro 256GB 黑色" },
    { locale: "zh_CN", storeNumber: "R532", storeTitle: "杭州万象城", partNumber: "B", productName: "iPhone 18 Pro 512GB 银色" },
    { locale: "zh_CN", storeNumber: "R532", storeTitle: "杭州万象城", partNumber: "C", productName: "iPhone 18 Pro Max 512GB 银色" },
    { locale: "zh_CN", storeNumber: "R471", storeTitle: "杭州西湖", partNumber: "A", productName: "iPhone 18 Pro 256GB 黑色" },
    { locale: "zh_CN", storeNumber: "R471", storeTitle: "杭州西湖", partNumber: "B", productName: "iPhone 18 Pro 512GB 银色" },
    { locale: "zh_CN", storeNumber: "R471", storeTitle: "杭州西湖", partNumber: "C", productName: "iPhone 18 Pro Max 512GB 银色" },
  ]]);
  assert.deepEqual(app.choice("选择机型").values, []);
  assert.deepEqual(app.choice("选择自提门店").values, ["R532", "R471"]);
});

test("switching category or region clears the phone selection and preserves other categories", () => {
  const app = phoneSelectionApp();
  app.choice("选择品类").onChange("ipad");
  assert.equal(app.choice("选择型号").value, "");
  assert.deepEqual(app.choice("选择型号").options, [{ value: "D", label: "iPad Pro 256GB 银色" }]);
  assert.equal(app.addButton().disabled, true);
  app.choice("选择型号").onChange("D");
  assert.equal(app.addButton().disabled, false);
  app.choice("选择品类").onChange("iphone");
  assert.deepEqual(app.choice("选择机型").values, []);
  assert.equal(app.addButton().disabled, true);

  app.choice("选择机型").onChange(["iphone18pro"]);
  app.choice("选择容量").onChange(["512GB"]);
  app.choice("选择颜色").onChange(["银色"]);
  app.choice("选择地区").onChange("ja_JP");
  assert.deepEqual(app.choice("选择自提门店").values, []);
  assert.deepEqual(app.choice("选择机型").values, []);
  assert.deepEqual(app.choice("选择容量").values, []);
  assert.deepEqual(app.choice("选择颜色").values, []);
  assert.equal(app.addButton().disabled, true);
});

test("selecting several stores adds one target per store, skips existing ones, and keeps the stores selected", async () => {
  const app = phoneSelectionApp();
  app.state.stores = [
    { number: "R532", title: "杭州万象城" },
    { number: "R471", title: "杭州西湖" },
    { number: "R359", title: "上海南京东路" },
  ];
  // 上海南京东路的这一台已经在监控列表里了，再加不该重复。
  app.state.rows = [{
    target: { locale: "zh_CN", storeNumber: "R359", storeTitle: "上海南京东路", partNumber: "B", productName: "iPhone 18 Pro 512GB 银色" },
    availability: { kind: "out_of_stock" }, lastCheckedMs: null, consecutiveFailures: 0,
  }];
  app.choice("选择自提门店").onChange(["R532", "R471", "R359"]);
  assert.equal(app.addButton().disabled, false);
  app.addButton().onClick();
  await new Promise(setImmediate);
  assert.deepEqual(app.addedTargets, [[
    { locale: "zh_CN", storeNumber: "R359", storeTitle: "上海南京东路", partNumber: "B", productName: "iPhone 18 Pro 512GB 银色" },
    { locale: "zh_CN", storeNumber: "R532", storeTitle: "杭州万象城", partNumber: "B", productName: "iPhone 18 Pro 512GB 银色" },
    { locale: "zh_CN", storeNumber: "R471", storeTitle: "杭州西湖", partNumber: "B", productName: "iPhone 18 Pro 512GB 银色" },
  ]]);
  // 型号清掉、门店保留，接着给同一批门店加下一个型号最顺手。
  assert.deepEqual(app.choice("选择机型").values, []);
  assert.deepEqual(app.choice("选择自提门店").values, ["R532", "R471", "R359"]);
  assert.equal(app.addButton().disabled, true);
});

test("the add button stays disabled until at least one store is selected", () => {
  const app = phoneSelectionApp();
  app.choice("选择自提门店").onChange([]);
  assert.equal(app.addButton().disabled, true);
  app.choice("选择自提门店").onChange(["R532"]);
  assert.equal(app.addButton().disabled, false);
});
