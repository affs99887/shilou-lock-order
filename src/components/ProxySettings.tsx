import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { saveProxySettings } from "@/lib/store";
import type { ProxyMode, Settings } from "@/lib/types";

/** 不走本机网络时可选的查询间隔（秒）。走本机网络时后端固定 60 秒。 */
const FASTEST_INTERVAL = 5;
const DEFAULT_INTERVAL = 60;
const FAST_INTERVALS = [FASTEST_INTERVAL, 10, 15, 30, DEFAULT_INTERVAL];

const MODES: { value: ProxyMode; label: string; note: string }[] = [
  {
    value: "mixed",
    label: "本机网络",
    note: "固定每 60 秒查一次；填了代理时和代理轮流用。",
  },
  {
    value: "pool",
    label: "代理轮流（机场节点）",
    note: "只用代理，不走本机网络。被苹果拦截的代理单独暂停 5～30 分钟，连不上的先跳过，其余代理继续查。每个节点在 Clash 里开一个本地端口，逐行填进来。",
  },
  {
    value: "tunnel",
    label: "隧道代理（每次换 IP）",
    note: "只用代理，不走本机网络。每次查询新建连接、换一个出口 IP，被拦当场换 IP 重查。代理要选「按请求换 IP」的，几分钟才换一次的不适用。",
  },
];

/** 和后端 normalize_proxies 一样按换行、分号、空白拆开并去重；格式由后端把关。 */
function splitProxies(text: string): string[] {
  return [...new Set(text.split(/[\s;]+/).map((s) => s.trim()).filter(Boolean))];
}

function describeSaved(saved: Settings): string {
  const n = saved.proxies.length;
  const every = `每 ${saved.intervalSeconds} 秒查一次`;
  if (n > 0 && saved.proxyMode === "pool") return `已生效：${every}，${n} 个代理轮流用，不走本机网络。`;
  if (n > 0 && saved.proxyMode === "tunnel") return `已生效：${every}，每次换一个出口 IP，不走本机网络。`;
  if (n > 0) return `已生效：${n} 个代理和本机网络轮流使用，${every}。`;
  return `已生效：只用本机网络，${every}。`;
}

export function ProxySettings({ settings }: { settings: Settings }) {
  const [draft, setDraft] = useState<string | null>(null);
  const [mode, setMode] = useState<ProxyMode | null>(null);
  const [seconds, setSeconds] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const inFlight = useRef(false);

  const text = draft ?? settings.proxies.join("\n");
  const chosen = mode ?? settings.proxyMode ?? "mixed";
  const proxyOnly = chosen !== "mixed";
  const interval = seconds ?? settings.intervalSeconds;

  function onModeChange(next: ProxyMode) {
    setMode(next);
    setMessage("");
    setError("");
    // 改成不走本机网络时直接给最快的 5 秒；之前选过别的快间隔就沿用。
    if (next !== "mixed" && seconds === null && settings.intervalSeconds >= DEFAULT_INTERVAL) {
      setSeconds(FASTEST_INTERVAL);
    }
  }

  async function save() {
    if (inFlight.current) return;
    setMessage("");
    setError("");
    const proxies = splitProxies(text);
    if (proxyOnly && proxies.length === 0) {
      setError("选「代理轮流」或「隧道代理」前，请先填上代理地址。");
      return;
    }
    inFlight.current = true;
    setBusy(true);
    try {
      const saved = await saveProxySettings({
        proxies,
        proxyMode: chosen,
        intervalSeconds: proxyOnly ? interval : DEFAULT_INTERVAL,
      });
      setDraft(null);
      setMode(null);
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
    <p className="section-note">Apple 按出口 IP 累计查询次数，同一个 IP 查得太勤会被拦截（HTTP 541）。把查询分散到多个代理的 IP 上，就能查得更勤。</p>
    <label htmlFor="proxy-list" className="mb-2 block text-sm font-medium">代理地址（每行一个）</label>
    <textarea
      id="proxy-list"
      rows={4}
      spellCheck={false}
      autoComplete="off"
      disabled={busy}
      value={text}
      placeholder={"http://127.0.0.1:18001\nhttp://用户名:密码@隧道地址:端口"}
      onChange={(e) => { setDraft(e.target.value); setMessage(""); setError(""); }}
      className="border-input dark:bg-input/30 focus-visible:border-ring focus-visible:ring-ring/50 w-full min-w-0 rounded-md border bg-transparent px-3 py-2 font-mono text-sm shadow-xs outline-none select-text focus-visible:ring-[3px] disabled:opacity-50"
    />
    <fieldset className="mt-4 grid gap-3" disabled={busy}>
      <legend className="mb-2 text-sm font-medium">查询方式</legend>
      {MODES.map((m) => (
        <div key={m.value} className="flex items-start gap-2">
          <input
            type="radio"
            id={`proxy-mode-${m.value}`}
            name="proxy-mode"
            className="mt-1"
            checked={chosen === m.value}
            onChange={() => onModeChange(m.value)}
          />
          <label htmlFor={`proxy-mode-${m.value}`} className="text-sm">
            <span className="font-medium">{m.label}</span>
            <span className="text-muted-foreground block text-xs leading-5">{m.note}</span>
          </label>
        </div>
      ))}
    </fieldset>
    {proxyOnly && <div className="mt-4 flex items-center gap-3">
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
    <div className="mt-4 flex gap-3"><Button disabled={busy} onClick={() => void save()}>{busy ? "保存中…" : "保存代理设置"}</Button></div>
    {message && <p className="section-note" role="status">{message}</p>}
    {error && <p className="mt-3 text-sm text-destructive" role="alert">{error}</p>}
  </section>;
}
