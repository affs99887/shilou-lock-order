import { useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { normalizeBarkAddress } from "@/lib/bark-address";
import { saveBarkAddress } from "@/lib/store";

export function BarkSettings({ saved }: { saved: string }) {
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  async function commit(test: boolean) {
    if (inFlight.current) return;
    setMessage(""); setError("");
    let normalized: string;
    try {
      normalized = normalizeBarkAddress(draft ?? saved);
      if (test && !normalized) throw new Error("先粘贴 Bark 链接，再测试提醒。");
    } catch (e) { setError(e instanceof Error ? e.message : "无法识别链接"); return; }
    inFlight.current = true; setBusy(true);
    try {
      await saveBarkAddress(normalized);
      setDraft(normalized);
      setMessage(normalized ? "已识别并保存 Bark 地址" : "已关闭 Bark 推送");
      if (test) { await invoke("test_notify"); setMessage("测试请求已发送，请查看手机通知"); }
    } catch { setMessage(""); setError(test ? "保存或测试失败，请核对链接与网络后重试。" : "保存失败，请重试。"); }
    finally { inFlight.current = false; setBusy(false); }
  }
  return <section className="checkout-panel rounded-xl border bg-card p-6">
    <h2 className="text-lg font-semibold">手机提醒</h2>
    <p className="section-note">从 Bark App 复制链接，直接粘贴到这里即可。</p>
    <label htmlFor="bark-address" className="mb-2 block text-sm font-medium">Bark 链接地址</label>
    <Input id="bark-address" type="password" autoComplete="off" spellCheck={false} className="select-text" disabled={busy} value={draft ?? saved} placeholder="https://api.day.app/你的设备密钥/推送内容" onChange={e => { setDraft(e.target.value); setMessage(""); setError(""); }} />
    <div className="mt-4 flex gap-3"><Button disabled={busy} onClick={() => void commit(false)}>{busy ? "处理中…" : "保存地址"}</Button><Button variant="outline" disabled={busy} onClick={() => void commit(true)}>保存并测试提醒</Button></div>
    {message && <p className="section-note" role="status">{message}</p>}
    {error && <p className="mt-3 text-sm text-destructive" role="alert">{error}</p>}
    {saved.includes(";") && <p className="section-note">原设置含多个 Bark 地址；保存一个新地址后将替换原列表。</p>}
  </section>;
}
