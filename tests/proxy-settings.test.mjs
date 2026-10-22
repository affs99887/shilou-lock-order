import test from 'node:test';
import assert from 'node:assert/strict';
import { loadSource } from './load-source.mjs';

const TUNNEL = 'http://user:pass@tunnel.example:15818';
const NODES = ['http://127.0.0.1:18001', 'http://127.0.0.1:18002', 'http://127.0.0.1:18003'];

function harness({ settings = {}, saveFails = false } = {}) {
  const hooks = []; let cursor = 0; const calls = []; const ref = { current: false };
  const base = { locale: 'zh_CN', targets: [], intervalSeconds: 60, barkUrl: '', feishuWebhook: '',
    soundEnabled: false, openBagOnHit: true, proxies: [], proxyMode: 'mixed', ...settings };
  const C = loadSource('src/components/ProxySettings.tsx', (s) => {
    if (s === 'react') return { useRef: () => ref, useState(initial) { const i = cursor++; if (!(i in hooks)) hooks[i] = initial; return [hooks[i], (v) => { hooks[i] = v; }]; } };
    if (s === '@/lib/store') return { saveProxySettings: async (patch) => {
      calls.push(patch);
      if (saveFails) throw new Error('代理设置未生效：代理地址无效 http://bad:1');
      // 模拟后端：丢掉认不出的地址；没有可用代理或走本机网络时间隔回到 60 秒（真实规则见 monitor_policy.rs）。
      const proxies = patch.proxies.filter((p) => /^(https?|socks5h?):\/\/\S+$/.test(p));
      const fast = patch.proxyMode !== 'mixed' && proxies.length > 0;
      return { ...base, ...patch, proxies, intervalSeconds: fast ? Math.min(Math.max(patch.intervalSeconds, 5), 60) : 60 };
    } };
    if (s.startsWith('@/components/')) return new Proxy({}, { get: (_, n) => String(n) });
  }).ProxySettings;
  function nodes() { cursor = 0; const all = []; function walk(e) { if (!e || typeof e !== 'object') return; if (e.type) all.push(e); for (const c of [e.props?.children ?? e].flat()) if (c !== e) walk(c); } walk(C({ settings: base })); return all; }
  const find = (pred) => nodes().find(pred);
  return {
    hooks, calls, nodes,
    type: (value) => find((e) => e.type === 'textarea').props.onChange({ target: { value } }),
    choose: (mode) => find((e) => e.props?.id === `proxy-mode-${mode}`).props.onChange(),
    checked: () => nodes().filter((e) => e.type === 'input' && e.props.checked).map((e) => e.props.id),
    select: () => find((e) => e.type === 'select'),
    save: async () => { find((e) => e.type === 'Button').props.onClick(); await new Promise(setImmediate); },
    message: () => find((e) => e.props?.role === 'status')?.props.children,
    error: () => find((e) => e.props?.role === 'alert')?.props.children,
  };
}

test('choosing the node pool defaults to five seconds and keeps the local network out', async () => {
  const a = harness();
  assert.deepEqual(a.checked(), ['proxy-mode-mixed']);
  assert.equal(a.select(), undefined, '走本机网络时没有间隔可选');
  a.type(NODES.join('\n'));
  a.choose('pool');
  assert.deepEqual(a.checked(), ['proxy-mode-pool']);
  assert.equal(a.select().props.value, 5);
  await a.save();
  assert.deepEqual(a.calls, [{ proxies: NODES, proxyMode: 'pool', intervalSeconds: 5 }]);
  assert.match(a.message(), /每 5 秒查一次，3 个代理轮流用，不走本机网络/);
  assert.equal(a.error(), undefined);
});

test('tunnel mode saves one deduplicated address and promises a new exit IP per query', async () => {
  const a = harness();
  a.type(`${TUNNEL}\n\n${TUNNEL}`);
  a.choose('tunnel');
  await a.save();
  assert.deepEqual(a.calls, [{ proxies: [TUNNEL], proxyMode: 'tunnel', intervalSeconds: 5 }]);
  assert.match(a.message(), /每次换一个出口 IP，不走本机网络/);
});

test('an earlier fast interval is kept when a proxy-only mode is already chosen', () => {
  const a = harness({ settings: { proxies: [TUNNEL], proxyMode: 'tunnel', intervalSeconds: 10 } });
  assert.equal(a.select().props.value, 10);
});

test('proxy-only modes without any address are refused before saving', async () => {
  const a = harness();
  a.choose('pool');
  await a.save();
  assert.equal(a.calls.length, 0);
  assert.match(a.error(), /先填上代理地址/);
});

test('going back to the local network always asks for the one-minute interval', async () => {
  const a = harness({ settings: { proxies: [TUNNEL], proxyMode: 'pool', intervalSeconds: 5 } });
  a.choose('mixed');
  assert.equal(a.select(), undefined);
  await a.save();
  assert.deepEqual(a.calls, [{ proxies: [TUNNEL], proxyMode: 'mixed', intervalSeconds: 60 }]);
  assert.match(a.message(), /1 个代理和本机网络轮流使用，每 60 秒/);
});

test('unrecognised addresses are reported and leave the local network at one minute', async () => {
  const a = harness();
  a.type('ftp://nope:21');
  a.choose('tunnel');
  await a.save();
  assert.match(a.message(), /只用本机网络，每 60 秒/);
  assert.match(a.error(), /1 个地址无法识别/);
});

test('a failed save keeps the draft and says nothing took effect', async () => {
  const a = harness({ saveFails: true });
  a.type('http://bad:1');
  await a.save();
  assert.equal(a.message(), undefined);
  assert.match(a.error(), /保存失败，原设置未改动/);
  assert.equal(a.hooks[0], 'http://bad:1', '草稿留着，方便改正后重试');
});
