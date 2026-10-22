import test from 'node:test';
import assert from 'node:assert/strict';
import { loadSource } from './load-source.mjs';

const TUNNEL = 'http://user:pass@tunnel.example:15818';

function harness({ settings = {}, saveFails = false } = {}) {
  const hooks = []; let cursor = 0; const calls = []; const ref = { current: false };
  const base = { locale: 'zh_CN', targets: [], intervalSeconds: 60, barkUrl: '', feishuWebhook: '',
    soundEnabled: false, openBagOnHit: true, proxies: [], rotateProxies: false, ...settings };
  const C = loadSource('src/components/ProxySettings.tsx', (s) => {
    if (s === 'react') return { useRef: () => ref, useState(initial) { const i = cursor++; if (!(i in hooks)) hooks[i] = initial; return [hooks[i], (v) => { hooks[i] = v; }]; } };
    if (s === '@/lib/store') return { saveProxySettings: async (patch) => {
      calls.push(patch);
      if (saveFails) throw new Error('代理设置未生效：代理地址无效 http://bad:1');
      // 模拟后端：丢掉认不出的地址；没有可用代理时间隔回到 60 秒（真实规则见 monitor_policy.rs）。
      const proxies = patch.proxies.filter((p) => /^(https?|socks5h?):\/\/\S+$/.test(p));
      const fast = patch.rotateProxies && proxies.length > 0;
      return { ...base, ...patch, proxies, intervalSeconds: fast ? Math.min(Math.max(patch.intervalSeconds, 5), 60) : 60 };
    } };
    if (s.startsWith('@/components/')) return new Proxy({}, { get: (_, n) => String(n) });
  }).ProxySettings;
  function nodes() { cursor = 0; const all = []; function walk(e) { if (!e || typeof e !== 'object') return; if (e.type) all.push(e); for (const c of [e.props?.children ?? e].flat()) if (c !== e) walk(c); } walk(C({ settings: base })); return all; }
  const find = (pred) => nodes().find(pred);
  return {
    hooks, calls, nodes,
    type: (value) => find((e) => e.type === 'textarea').props.onChange({ target: { value } }),
    rotate: (on) => find((e) => e.type === 'Switch').props.onCheckedChange(on),
    select: () => find((e) => e.type === 'select'),
    save: async () => { find((e) => e.type === 'Button').props.onClick(); await new Promise(setImmediate); },
    message: () => find((e) => e.props?.role === 'status')?.props.children,
    error: () => find((e) => e.props?.role === 'alert')?.props.children,
  };
}

test('turning on rotation defaults to five seconds and saves proxy-only fast polling', async () => {
  const a = harness();
  assert.equal(a.select(), undefined, '不换 IP 时没有间隔可选');
  a.type(`${TUNNEL}\n\n${TUNNEL}`);
  a.rotate(true);
  assert.equal(a.select().props.value, 5);
  await a.save();
  assert.deepEqual(a.calls, [{ proxies: [TUNNEL], rotateProxies: true, intervalSeconds: 5 }]);
  assert.match(a.message(), /每 5 秒查一次，每次换一个出口 IP，不走本机网络/);
  assert.equal(a.error(), undefined);
});

test('an earlier fast interval is kept when rotation is already on', () => {
  const a = harness({ settings: { proxies: [TUNNEL], rotateProxies: true, intervalSeconds: 10 } });
  assert.equal(a.select().props.value, 10);
});

test('rotation without any address is refused before saving', async () => {
  const a = harness();
  a.rotate(true);
  await a.save();
  assert.equal(a.calls.length, 0);
  assert.match(a.error(), /隧道代理地址/);
});

test('turning rotation off always asks for the one-minute interval', async () => {
  const a = harness({ settings: { proxies: [TUNNEL], rotateProxies: true, intervalSeconds: 5 } });
  a.rotate(false);
  assert.ok(a.nodes().some((e) => e.type === 'p' && String(e.props.children).includes('固定每 60 秒')));
  await a.save();
  assert.deepEqual(a.calls, [{ proxies: [TUNNEL], rotateProxies: false, intervalSeconds: 60 }]);
  assert.match(a.message(), /1 个代理和本机网络轮流使用，每 60 秒/);
});

test('unrecognised addresses are reported and leave the local network at one minute', async () => {
  const a = harness();
  a.type('ftp://nope:21');
  a.rotate(true);
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
