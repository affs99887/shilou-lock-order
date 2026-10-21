import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { type Target, targetKey } from "@/lib/types";

type Status = { armed: boolean; busy: boolean; stopping: boolean; orderNeedsReview?: boolean;
  plan: { auto_submit_order?: boolean; [key: string]: unknown } };
const occupied = (s: Status | null) => !s || s.armed || s.busy || s.stopping || !!s.orderNeedsReview;

export function useInventoryPurchase() {
  const [status, setStatus] = useState<Status | null>(null);
  const [pending, setPending] = useState(false);
  const [confirmationTarget, setConfirmationTarget] = useState<Target | null>(null);
  const [error, setError] = useState("");
  const latest = useRef<Status | null>(null);
  const sending = useRef(false);
  const confirmation = useRef<{ target: Target; plan: string } | null>(null);
  function receive(value: Status) { latest.current = value; setStatus(value); }
  useEffect(() => {
    let active = true;
    const unlisten = listen<Status>("checkout://status", e => { if (active) receive(e.payload); });
    void invoke<Status>("checkout_command", { op: "status" }).then(s => { if (active) receive(s); })
      .catch(e => { if (active) setError(String(e)); });
    return () => { active = false; void unlisten.then(f => f()); };
  }, []);
  async function purchase(target: Target, confirmed = false) {
    if (sending.current || occupied(latest.current)) return;
    if (confirmation.current && !confirmed) return;
    if (confirmed && targetKey(confirmation.current?.target ?? target) !== targetKey(target)) return;
    sending.current = true; setPending(true); setError("");
    try {
      const current = await invoke<Status>("checkout_command", { op: "status" });
      receive(current);
      if (occupied(current)) { confirmation.current = null; setConfirmationTarget(null); return; }
      if (confirmed && (!confirmation.current || confirmation.current.plan !== JSON.stringify(current.plan))) {
        confirmation.current = null; setConfirmationTarget(null);
        throw new Error("选购方案已变更，请核对后重新点击立即选购");
      }
      if (current.plan.auto_submit_order && !confirmed) {
        confirmation.current = { target, plan: JSON.stringify(current.plan) };
        setConfirmationTarget(target); return;
      }
      confirmation.current = null; setConfirmationTarget(null);
      receive(await invoke<Status>("checkout_command", { op: "purchase", payload: {
        targetKey: targetKey(target), confirmSubmission: confirmed && !!current.plan.auto_submit_order,
      } }));
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { sending.current = false; setPending(false); }
  }
  return { disabled: occupied(status) || pending || confirmationTarget !== null, pending,
    confirmationTarget, error, purchase,
    confirm: () => { if (confirmation.current) void purchase(confirmation.current.target, true); },
    cancel: () => { if (!sending.current) { confirmation.current = null; setConfirmationTarget(null); } },
  };
}
