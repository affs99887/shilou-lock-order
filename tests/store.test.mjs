import assert from "node:assert/strict";
import test from "node:test";
import { loadSource } from "./load-source.mjs";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function catalog(locale, revision = "initial") {
  return {
    stores: [{ number: `${locale}-store`, name: locale, title: `${locale} store` }],
    products: [{ partNumber: `${locale}-${revision}`, title: revision, category: "iphone" }],
  };
}

function setup() {
  const calls = [];
  const handlers = {};
  const events = new Map();
  const store = loadSource("src/lib/store.ts", (specifier) => {
    if (specifier === "@tauri-apps/api/core") return {
      invoke: async (command, args) => {
        calls.push({ command, args });
        if (handlers[command]) return handlers[command](args);
        if (command === "save_settings") return args.settings;
        if (command === "list_stores") return catalog(args.locale).stores;
        if (command === "list_products") return catalog(args.locale).products;
        throw new Error(`Unexpected command: ${command}`);
      },
    };
    if (specifier === "@tauri-apps/api/event") return {
      listen: async (channel, callback) => {
        events.set(channel, callback);
        return () => events.delete(channel);
      },
    };
  });
  return { ...store, snapshot: store.watcherStore.getSnapshot, calls, handlers,
    emit: payload => events.get("watcher://event")({ payload }),
    emitCommunity: payload => events.get("community://status")({ payload }),
    emitCloud: payload => events.get("cloud://event")({ payload }) };
}

async function monitoringApp() {
  const app = setup();
  Object.assign(app.handlers, {
    list_regions: () => [], list_categories: () => [], get_snapshot: () => [],
    get_settings: () => app.snapshot().settings, is_running: () => false,
    check_for_update: () => null,
  });
  await app.connect();
  return app;
}

test("shared hints and failures stay in logs without replacing official inventory", async () => {
  const app = await monitoringApp();
  const row = { target: { storeTitle: "示例门店", productName: "示例型号" },
    availability: { kind: "unknown", reason: "blocked" } };
  app.emit({ type: "cycleComplete", healthy: false, snapshot: [row], nextCheckInSecs: 60, paced: false });
  const hint = { running: true, lastSignal: { target: row.target, source: "sunbelife_aggregate" }, error: null };
  app.emitCommunity(hint);
  app.emitCommunity({ ...hint, error: "共享接口暂时不可用" });
  app.emitCommunity({ ...hint, error: "共享接口暂时不可用" });
  assert.deepEqual(app.snapshot().rows, [row]);
  assert.equal(app.snapshot().logs.filter(line => line.includes("共享接口暂时不可用")).length, 1);
  app.emitCommunity(hint);
  assert.match(app.snapshot().logs.at(-1), /共享线索连接已恢复/);
  app.disconnect();
});

test("a delayed shared-status snapshot cannot overwrite a newer background event", async () => {
  const app = setup();
  const pending = deferred();
  Object.assign(app.handlers, {
    list_regions: () => [], list_categories: () => [], get_snapshot: () => [],
    get_settings: () => app.snapshot().settings, is_running: () => false,
    check_for_update: () => null, get_community_status: () => pending.promise,
  });
  await app.connect();
  const current = { running: true, error: "连接暂不可用", lastCheckedMs: 20 };
  app.emitCommunity(current);
  pending.resolve({ running: false, error: null, lastCheckedMs: 10 });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(app.snapshot().community, current);
  assert.ok(!app.snapshot().logs.some(line => line.includes("共享线索连接已恢复")));
  app.disconnect();
});

test("blocked events preserve the cause, cooldown and unknown stock until a healthy cycle", async () => {
  const app = await monitoringApp();
  await app.saveSettings({ ...app.snapshot().settings, monitorMode: "local" });
  const detail = "HTTP 541；直连已进入冷却，5 分钟后自动重试一次";
  const trouble = { reason: `查询失败：${detail}`, advice: "try_another_network" };
  const row = { target: { storeTitle: "示例门店", productName: "示例型号" },
    availability: { kind: "unknown", reason: "blocked", detail } };
  app.emit({ type: "trouble", ...trouble });
  app.emit({ type: "stateChanged", state: row });
  app.emit({ type: "cycleComplete", healthy: false, snapshot: [row], nextCheckInSecs: 10, paced: false });
  assert.deepEqual(app.snapshot().trouble, trouble);
  assert.deepEqual(app.snapshot().rows, [row]);
  assert.ok(app.snapshot().logs.some(line => line.includes(trouble.reason)));
  assert.ok(app.snapshot().logs.some(line => line.includes(`示例门店 示例型号：请求被 Apple 拦截：${detail}`)));
  assert.ok(!app.snapshot().logs.join("").includes("调度保护"));
  const recovered = { ...row, availability: { kind: "out_of_stock" } };
  app.emit({ type: "cycleComplete", healthy: true, snapshot: [recovered], nextCheckInSecs: 5, paced: false });
  assert.equal(app.snapshot().trouble, null);
  assert.deepEqual(app.snapshot().rows, [recovered]);
  assert.match(app.snapshot().logs.at(-1), /查询已恢复正常/);
  app.disconnect();
});

test("temporary API errors keep their HTTP code instead of a generic protection message", async () => {
  const app = await monitoringApp();
  for (const code of [429, 503]) {
    const reason = `示例门店 查询暂时不可用：HTTP ${code}，将退避后重试`;
    app.emit({ type: "trouble", reason, advice: null });
    assert.equal(app.snapshot().trouble.reason, reason);
    assert.ok(app.snapshot().logs.at(-1).includes(reason));
  }
  app.disconnect();
});

test("budget wait logs explain the delay once without marking inventory as failed", async () => {
  const app = await monitoringApp();
  const event = { type: "cycleComplete", healthy: true, snapshot: [], nextCheckInSecs: 137, paced: true };
  app.emit(event);
  assert.match(app.snapshot().logs.at(-1), /请求预算不足.*137 秒/);
  assert.equal(app.snapshot().trouble, null);
  const count = app.snapshot().logs.length;
  app.emit(event);
  assert.equal(app.snapshot().logs.length, count);
  app.disconnect();
});

test("single Bark save preserves targets, proxies and other settings", async () => {
 const app=setup();const prior={...app.snapshot().settings,proxies:['http://127.0.0.1:7890'],intervalSeconds:60,feishuWebhook:'https://example.test/existing',targets:[{partNumber:'TEST'}]};
 await app.saveSettings(prior);await app.saveBarkAddress('https://api.day.app/NewKey');
 assert.deepEqual(app.snapshot().settings,{...prior,barkUrl:'https://api.day.app/NewKey'});
});
test("Bark save failure propagates and preserves old value without leaking endpoint", async () => {
 const app=setup();const before=app.snapshot().settings;
 app.handlers.save_settings=()=>{throw Error('https://api.day.app/SECRET');};
 await assert.rejects(app.saveBarkAddress('https://api.day.app/NEW'));
 assert.deepEqual(app.snapshot().settings,before);assert.ok(!app.snapshot().logs.join('').includes('SECRET'));
});

test("a completed refresh for the previous locale cannot replace the selected catalog", async () => {
  const app = setup();
  const refresh = deferred();
  app.handlers.refresh_products = () => refresh.promise;
  await app.loadCatalog("zh_CN");
  const pending = app.refreshProducts();
  await app.changeLocale("ja_JP");
  const count = app.calls.length;
  refresh.resolve(12);
  await pending;
  assert.equal(app.snapshot().settings.locale, "ja_JP");
  assert.deepEqual(app.snapshot().products, catalog("ja_JP").products);
  assert.equal(app.calls.length, count, "no obsolete catalog request should be started");
  assert.equal(app.snapshot().refreshing, false);
});

test("a pending catalog cannot repopulate its old locale after switching", async () => {
  const app = setup();
  const oldProducts = deferred();
  app.handlers.list_products = ({ locale }) =>
    locale === "zh_CN" ? oldProducts.promise : catalog(locale).products;
  const pending = app.loadCatalog("zh_CN");
  await app.changeLocale("ja_JP");
  oldProducts.resolve(catalog("zh_CN").products);
  await pending;
  assert.deepEqual(app.snapshot().stores, catalog("ja_JP").stores);
  assert.deepEqual(app.snapshot().products, catalog("ja_JP").products);
});

test("the newest request wins when same-locale catalog requests finish out of order", async () => {
  const app = setup();
  const older = deferred();
  app.handlers.list_products = () => older.promise;
  const pending = app.loadCatalog("zh_CN");
  app.handlers.list_products = () => catalog("zh_CN", "new").products;
  await app.loadCatalog("zh_CN");
  older.resolve(catalog("zh_CN", "old").products);
  await pending;
  assert.deepEqual(app.snapshot().products, catalog("zh_CN", "new").products);
});

test("a stale request failure cannot clear a newer catalog or log a current failure", async () => {
  const app = setup();
  const older = deferred();
  app.handlers.list_products = () => older.promise;
  const pending = app.loadCatalog("zh_CN");
  app.handlers.list_products = () => catalog("zh_CN", "new").products;
  await app.loadCatalog("zh_CN");
  older.reject(new Error("obsolete failure"));
  await pending;
  assert.deepEqual(app.snapshot().products, catalog("zh_CN", "new").products);
  assert.equal(app.snapshot().logs.length, 0);
});

test("switching locale immediately clears old choices while the new catalog loads", async () => {
  const app = setup();
  await app.loadCatalog("zh_CN");
  const loading = deferred();
  const requested = deferred();
  app.handlers.list_products = () => { requested.resolve(); return loading.promise; };
  const pending = app.changeLocale("ja_JP");
  await requested.promise;
  assert.equal(app.snapshot().settings.locale, "ja_JP");
  assert.deepEqual(app.snapshot().stores, []);
  assert.deepEqual(app.snapshot().products, []);
  loading.resolve(catalog("ja_JP").products);
  await pending;
  assert.deepEqual(app.snapshot().products, catalog("ja_JP").products);
});

test("a failed locale save keeps the original locale and catalog without loading new choices", async () => {
  const app = setup();
  await app.loadCatalog("zh_CN");
  const count = app.calls.length;
  app.handlers.save_settings = () => { throw new Error("disk full"); };
  await app.changeLocale("ja_JP");
  assert.equal(app.snapshot().settings.locale, "zh_CN");
  assert.deepEqual(app.snapshot().products, catalog("zh_CN").products);
  assert.deepEqual(app.calls.slice(count).map((call) => call.command), ["save_settings"]);
  assert.match(app.snapshot().logs.at(-1), /保存设置失败.*disk full/);
});

test("a delayed settings save that restores the old locale also restores its matching catalog", async () => {
  const app = setup();
  await app.loadCatalog("zh_CN");
  const previousSettings = app.snapshot().settings;
  const localeSave = deferred();
  const soundSave = deferred();
  app.handlers.save_settings = ({ settings }) =>
    settings.locale === "ja_JP" ? localeSave.promise : soundSave.promise;

  const switching = app.changeLocale("ja_JP");
  const soundSettings = { ...previousSettings, soundEnabled: false };
  const togglingSound = app.saveSettings(soundSettings);
  localeSave.resolve({ ...previousSettings, locale: "ja_JP" });
  await switching;
  assert.deepEqual(app.snapshot().products, catalog("ja_JP").products);

  soundSave.resolve(soundSettings);
  await togglingSound;
  assert.equal(app.snapshot().settings.locale, "zh_CN");
  assert.equal(app.snapshot().settings.soundEnabled, false);
  assert.deepEqual(app.snapshot().stores, catalog("zh_CN").stores);
  assert.deepEqual(app.snapshot().products, catalog("zh_CN").products);
});

test("a current catalog failure clears choices and reports its cause", async () => {
  const app = setup();
  await app.loadCatalog("zh_CN");
  app.handlers.list_products = () => { throw new Error("unreadable catalog"); };
  await app.loadCatalog("zh_CN");
  assert.deepEqual(app.snapshot().stores, []);
  assert.deepEqual(app.snapshot().products, []);
  assert.match(app.snapshot().logs.at(-1), /载入 zh_CN.*unreadable catalog/);
});

test("a failed refresh still reloads the current catalog to expose partial backend results", async () => {
  const app = setup();
  app.handlers.refresh_products = () => { throw new Error("one page failed"); };
  app.handlers.list_products = () => catalog("zh_CN", "partial").products;
  await app.refreshProducts();
  assert.deepEqual(app.snapshot().products, catalog("zh_CN", "partial").products);
  assert.equal(app.snapshot().refreshing, false);
  assert.match(app.snapshot().logs.at(-1), /更新型号列表失败.*one page failed/);
});

test("cloud results are logged once per store until they change, without repeating per-model failures", async () => {
  const app = await monitoringApp();
  assert.equal(app.snapshot().settings.monitorMode, "cloud");
  const row = { target: { locale: "zh_CN", storeNumber: "R575", storeTitle: "Apple 武汉", productName: "示例型号", partNumber: "A" },
    availability: { kind: "unknown", reason: "rate_limited" } };
  app.emit({ type: "cycleComplete", healthy: false, snapshot: [row], nextCheckInSecs: 60, paced: false });
  const before = app.snapshot().logs.length;
  app.emit({ type: "stateChanged", state: row });
  assert.equal(app.snapshot().logs.length, before, "云端失败已按门店写过，不再逐个型号写");
  app.emitCloud({ origin: "local", stage: "request", store: "R575", locale: "zh_CN", parts: ["A"] });
  assert.equal(app.snapshot().logs.length, before, "准备查询那条不写");
  const checked = { origin: "vss", stage: "response", store: "R575", locale: "zh_CN", parts: ["A", "B"], outcome: "checked",
    counts: { total: 2, inStock: 0, outOfStock: 2, unconfirmed: 0 } };
  app.emitCloud(checked);
  assert.match(app.snapshot().logs.at(-1), /云端 · Apple 武汉：所选 2 款都暂无自提现货/);
  app.emitCloud(checked);
  assert.equal(app.snapshot().logs.length, before + 1, "结果没变不重复写");
  app.emitCloud({ ...checked, outcome: "rate_limited", counts: undefined });
  assert.match(app.snapshot().logs.at(-1), /对方正在限流，库存未确认/);
  app.emitCloud({ ...checked, counts: { total: 2, inStock: 1, outOfStock: 0, unconfirmed: 1 } });
  assert.match(app.snapshot().logs.at(-1), /1 款有货，1 款未确认/);
  app.emit({ type: "runStateChanged", running: true });
  app.emitCloud({ ...checked, counts: { total: 2, inStock: 1, outOfStock: 0, unconfirmed: 1 } });
  assert.match(app.snapshot().logs.at(-1), /1 款有货/, "重新开始后同样的结果照常写一次");
  app.disconnect();
});

test("switching the stock source refreshes rows and reports failures without leaving the switch locked", async () => {
  const app = await monitoringApp();
  const saved = { ...app.snapshot().settings, monitorMode: "local" };
  app.handlers.set_monitor_mode = args => { assert.equal(args.mode, "local"); return saved; };
  app.handlers.get_snapshot = () => [];
  await app.setMonitorMode("local");
  assert.equal(app.snapshot().settings.monitorMode, "local");
  assert.equal(app.snapshot().running, false);
  assert.equal(app.snapshot().switching, false);
  assert.match(app.snapshot().logs.at(-1), /库存来源已切换为本机网络/);
  app.handlers.set_monitor_mode = () => { throw new Error("正在选购或登录时不能切换库存来源"); };
  await app.setMonitorMode("cloud");
  assert.equal(app.snapshot().settings.monitorMode, "local");
  assert.equal(app.snapshot().switching, false);
  assert.match(app.snapshot().logs.at(-1), /切换库存来源失败.*正在选购/);
  app.disconnect();
});

test("diagnostics export reports the folder and whether some records were lost", async () => {
  const app = await monitoringApp();
  app.handlers.export_monitor_diagnostics = () => ({ directory: "D:/下载/拾漏锁单诊断-1", droppedRecords: 0, writeError: null });
  await app.exportDiagnostics();
  assert.match(app.snapshot().logs.at(-1), /诊断记录已导出到 D:\/下载\/拾漏锁单诊断-1。$/);
  app.handlers.export_monitor_diagnostics = () => ({ directory: "D:/x", droppedRecords: 3, writeError: null });
  await app.exportDiagnostics();
  assert.match(app.snapshot().logs.at(-1), /部分记录没能保存/);
  app.disconnect();
});
