import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { type Target, targetKey } from "@/lib/types";

type Status = { armed: boolean; busy: boolean; stopping: boolean; orderNeedsReview?: boolean;
  plan: { auto_submit_order?: boolean; [key: string]: unknown } };
const occupied = (s: Status | null) => !s || s.armed || s.busy || s.stopping || !!s.orderNeedsReview;

/**
 * 库存表里「立即选购」按钮背后的逻辑。
 *
 * 点一下就直接开始，不弹窗确认：方案里勾了「自动下单，等待本人支付」，这次点击就是授权。
 * 连点、点不同行都只会发出一次选购；发命令前再向后台核对一次，按钮画出来之后才开始的选购也不会被重复启动。
 */
export function useInventoryPurchase() {
  const [status, setStatus] = useState<Status | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const latest = useRef<Status | null>(null);
  const sending = useRef(false);
  function receive(value: Status) { latest.current = value; setStatus(value); }
  useEffect(() => {
    let active = true;
    const unlisten = listen<Status>("checkout://status", e => { if (active) receive(e.payload); });
    void invoke<Status>("checkout_command", { op: "status" }).then(s => { if (active) receive(s); })
      .catch(e => { if (active) setError(String(e)); });
    return () => { active = false; void unlisten.then(f => f()); };
  }, []);
  async function purchase(target: Target) {
    if (sending.current || occupied(latest.current)) return;
    sending.current = true; setPending(true); setError("");
    try {
      const current = await invoke<Status>("checkout_command", { op: "status" });
      receive(current);
      if (occupied(current)) return;
      receive(await invoke<Status>("checkout_command", { op: "purchase", payload: {
        targetKey: targetKey(target), confirmSubmission: !!current.plan.auto_submit_order,
      } }));
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { sending.current = false; setPending(false); }
  }
  return { disabled: occupied(status) || pending, pending, error, purchase };
}
