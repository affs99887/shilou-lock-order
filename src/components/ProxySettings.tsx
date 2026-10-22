import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { saveProxySettings } from "@/lib/store";
import type { Settings } from "@/lib/types";

/** 换 IP 模式下可选的查询间隔（秒）。不换 IP 时后端固定 60 秒。 */
const FASTEST_INTERVAL = 5;
const DEFAULT_INTERVAL = 60;
const FAST_INTERVALS = [FASTEST_INTERVAL, 10, 15, 30, DEFAULT_INTERVAL];

/** 和后端 normalize_proxies 一样按换行、分号、空白拆开并去重；格式由后端把关。 */
function splitProxies(text: string): string[] {
  return [...new Set(text.split(/[\s;]+/).map((s) => s.trim()).filter(Boolean))];
}

function describeSaved(saved: Settings): string {
  if (saved.rotateProxies && saved.proxies.length > 0) {
    return `已生效：每 ${saved.intervalSeconds} 秒查一次，每次换一个出口 IP，不走本机网络。`;
  }
  if (saved.proxies.length > 0) {
    return `已生效：${saved.proxies.length} 个代理和本机网络轮流使用，每 ${saved.intervalSeconds} 秒查一次。`;
  }
  return `已生效：只用本机网络，每 ${saved.intervalSeconds} 秒查一次。`;
}

export function ProxySettings({ settings }: { settings: Settings }) {
  const [draft, setDraft] = useState<string | null>(null);
  const [rotate, setRotate] = useState<boolean | null>(null);
  const [seconds, setSeconds] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const inFlight = useRef(false);

  const text = draft ?? settings.proxies.join("\n");
  const rotating = rotate ?? settings.rotateProxies;
  const interval = seconds ?? settings.intervalSeconds;

  function onRotateChange(next: boolean) {
    setRotate(next);
    setMessage("");
    setError("");
    // 打开换 IP 时直接给最快的 5 秒；之前选过别的快间隔就沿用。
    if (next && seconds === null && settings.intervalSeconds >= DEFAULT_INTERVAL) setSeconds(FASTEST_INTERVAL);
  }

  async function save() {
    if (inFlight.current) return;
    setMessage("");
    setError("");
    const proxies = splitProxies(text);
    if (rotating && proxies.length === 0) {
      setError("打开「每次查询换 IP」前，请先填上隧道代理地址。");
      return;
    }
    inFlight.current = true;
    setBusy(true);
    try {
      const saved = await saveProxySettings({
        proxies,
        rotateProxies: rotating,
        intervalSeconds: rotating ? interval : DEFAULT_INTERVAL,
      });
      setDraft(null);
      setRotate(null);
      setSeconds(null);
      setMessage(describeSaved(saved));
      const dropped = proxies.length - saved.proxies.length;
      if (dropped > 0) {
        setError(`有 ${dropped} 个地址无法识别，已忽略（只支持 http、https、socks5、socks5h 开头的地址）。`);
      }
    } catch (e) {
      setError(`保存失败，原设置未改动：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return <section className="checkout-panel mt-4 rounded-xl border bg-card p-6">
    <h2 className="text-lg font-semibold">代理线路</h2>
    <p className="section-note">Apple 按出口 IP 累计查询次数，同一个 IP 查得太勤会被拦截（HTTP 541）。填上代理商给的隧道代理地址并打开「每次查询换 IP」，查询就会分散到不同的 IP 上。</p>
    <label htmlFor="proxy-list" className="mb-2 block text-sm font-medium">代理地址（每行一个）</label>
    <textarea
      id="proxy-list"
      rows={3}
      spellCheck={false}
      autoComplete="off"
      disabled={busy}
      value={text}
      placeholder="http://用户名:密码@隧道地址:端口"
      onChange={(e) => { setDraft(e.target.value); setMessage(""); setError(""); }}
      className="border-input dark:bg-input/30 focus-visible:border-ring focus-visible:ring-ring/50 w-full min-w-0 rounded-md border bg-transparent px-3 py-2 font-mono text-sm shadow-xs outline-none select-text focus-visible:ring-[3px] disabled:opacity-50"
    />
    <div className="mt-4 flex items-center gap-3">
      <Switch id="proxy-rotate" checked={rotating} disabled={busy} onCheckedChange={onRotateChange} />
      <label htmlFor="proxy-rotate" className="text-sm font-medium">每次查询换一个出口 IP（隧道代理）</label>
    </div>
    <p className="section-note">打开后：每次查询都新建连接和会话，从新的 IP 发出；某个 IP 被拦会立刻换 IP 重查，不再冷却等待。本机网络不参与查询，留给自动选购的浏览器。代理要选「按请求换 IP」的隧道代理，几分钟才换一次 IP 的不适用。</p>
    {rotating && <div className="flex items-center gap-3">
      <label htmlFor="proxy-interval" className="text-sm font-medium">查询间隔</label>
      <select
        id="proxy-interval"
        disabled={busy}
        value={interval}
        onChange={(e) => { setSeconds(Number(e.target.value)); setMessage(""); }}
        className="border-input dark:bg-input/30 h-9 rounded-md border bg-transparent px-3 text-sm shadow-xs"
      >
        {FAST_INTERVALS.map((s) => <option key={s} value={s}>{s} 秒</option>)}
      </select>
    </div>}
    {!rotating && <p className="section-note">不换 IP 时固定每 60 秒查一次，避免本机 IP 被拦。</p>}
    <div className="mt-4 flex gap-3"><Button disabled={busy} onClick={() => void save()}>{busy ? "保存中…" : "保存代理设置"}</Button></div>
    {message && <p className="section-note" role="status">{message}</p>}
    {error && <p className="mt-3 text-sm text-destructive" role="alert">{error}</p>}
  </section>;
}
