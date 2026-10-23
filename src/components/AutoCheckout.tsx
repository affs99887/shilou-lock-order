import { useEffect, useRef, useState } from "react";
import { LockKeyhole, Settings2 } from "lucide-react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { type Target, targetKey } from "@/lib/types";
import installmentPlans from "@/lib/installment-plans.json";
import { ContactProfile } from "@/components/ContactProfile";
import { ConfigModal } from "@/components/ConfigModal";
import checkoutRegions from "@/lib/checkout-regions.json";

type Plan = { max_price: number; quantity: number; applecare: string; payment: string;
  financing: string; pickup_dates: string[]; clear_bag_before_purchase: boolean;
  installment_option: string; installment_months: number; auto_submit_order: boolean;
  [key: string]: unknown };
type Status = { armed: boolean; busy: boolean; stopping: boolean; mode: string; stage: string;
  reason: string; elapsed: number; plan: Plan; submissionEnabled: boolean; orderNeedsReview?: boolean; platform?:string;
  intervention?: {kind:string;title:string;body:string} | null;
  error?:string; profileRequired?:boolean; activationPending?:boolean;
  /** 最近一次登录检查的结果；还没检查过为空。 */
  session?: string | null; loginTask?: boolean;
  /** 当前流程是「手动开始选购」。 */
  manualRun?: boolean };

export function AutoCheckout({ targets, expanded, onSettings }: { targets: Target[]; expanded?: boolean; onSettings?: () => void }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState("");
  const [confirmation, setConfirmation] = useState<"arm" | "manual" | "acknowledge_order" | null>(null);
  const dirty = useRef(false);
  const showPlan = expanded ?? open;
  const locked = pending || !!status?.busy || !!status?.armed || !!status?.stopping;
  const needsAttention = ["attention", "manual_region"].includes(status?.mode || "");
  const locale=String(plan?.checkout_locale||'zh_CN');
  const local=checkoutRegions.find(r=>r.locale===locale)!;
  const foreign=locale!=='zh_CN';
  const localTargets=targets.filter(t=>t.locale===locale);
  const datePolicy=String(plan?.pickup_date_policy || "specified");
  const dateList=plan?.pickup_dates.join("、") || "尚未设置日期";
  const pickupSummary=datePolicy === "by_model" ? `Duo 仅限 ${dateList}；其他型号最早可取` : datePolicy === "earliest" ? "所有型号：官网最早可取日期及最早可用时段" : `所有型号仅限：${dateList}`;
  useEffect(() => {
    let active = true;
    function receive(s: Status) {
      if (!active) return;
      setStatus(s);
      if (!dirty.current) setPlan(s.plan);
    }
    const unlisten = listen<Status>("checkout://status", e => receive(e.payload));
    void invoke<Status>("checkout_command", { op: "status" }).then(receive).catch(e => active && setError(String(e)));
    return () => { active = false; void unlisten.then(f => f()); };
  }, []);
  function edit(patch: Partial<Plan>) {
    dirty.current = true;
    setPlan(p => p ? { ...p, ...patch } : p);
  }
  async function command(op: string, confirmed = false) {
    if (!confirmed && (((op === "arm" || op === "manual") && plan?.auto_submit_order) || op === "acknowledge_order")) {
      setConfirmation(op as "arm" | "manual" | "acknowledge_order");
      return;
    }
    setConfirmation(null);
    setError(""); setPending(true);
    try {
      if ((op === "rehearse" || op === "manual") && status?.armed && !status.busy && !status.stopping) {
        // Explicit rehearsal replaces idle arming, never an active checkout.
        await invoke("checkout_command", { op: "disarm" });
      }
      if (["arm", "rehearse", "manual", "configure"].includes(op)) {
        if (!plan) throw new Error("选购组件尚未就绪");
        await invoke("checkout_command", { op: "configure", payload: { plan } });
        dirty.current = false;
      }
      const chosen = selected || (localTargets[0] && targetKey(localTargets[0]));
      const payload = op === "rehearse" ? { targetKey: chosen }
        : op === "manual" ? { targetKey: chosen, confirmSubmission: confirmed && !!plan?.auto_submit_order }
        : { confirmSubmission: op === "arm" && !!plan?.auto_submit_order, confirmed:op === "acknowledge_order" };
      const result = await invoke<Status>("checkout_command", { op, payload: op === "configure" ? { plan } : payload });
      setStatus(result);
      if (op === "import") { dirty.current = false; setPlan(result.plan); }
    } catch (e) { setError(String(e)); }
    finally { setPending(false); }
  }
  // 状态圆点的颜色：需要本人处理、已到核对页或待支付、正在跑、已启用、未启用。
  const tone = status?.stopping || needsAttention ? "attention"
    : ["ready", "awaiting_payment", "submitting"].includes(status?.mode || "") ? "ready"
    : status?.busy ? "busy" : status?.armed ? "armed" : "idle";
  const togglePlan = () => onSettings ? onSettings() : setOpen(!open);
  const closePlan = () => { if (showPlan) togglePlan(); };
  return <section className="checkout-panel compact-checkout shrink-0 rounded-xl border bg-card text-sm">
    <div className="checkout-heading">
      <h2><LockKeyhole size={16} /> 自动选购</h2>
      <button type="button" className="panel-toggle" aria-haspopup="dialog" aria-expanded={showPlan} aria-controls="purchase-plan" onClick={togglePlan}><Settings2 size={15} /> 方案设置</button>
    </div>
    <div className="checkout-status-grid">
      <div data-testid="checkout-execution-status" data-tone={tone}><span>自动选购</span><strong>{status?.stopping ? "停止中" : status?.activationPending ? "正在校验资料" : needsAttention ? "已暂停，需要处理" : status?.mode === "awaiting_payment" ? "订单待支付" : status?.mode === "submitting" ? "正在核对下单结果" : status?.mode === "ready" ? "已到订单核对页" : status?.busy ? (status.manualRun ? "手动选购执行中" : "正在执行") : status?.armed ? "已启用，等待命中" : "未启用"}</strong></div>
      <div data-testid="submission-status"><span>实际下单权限</span><strong>{status?.submissionEnabled ? "已授权自动下单" : "停在提交前"}</strong></div>
      <div><span>付款方案</span><strong>{!plan ? "读取中…" : plan.financing === "official" ? `${plan.installment_option || "未选渠道"} · ${plan.installment_months || "—"} 期` : plan.financing === "manual" ? "官网手动选择" : `${plan.payment} · 全款`}</strong></div>
    </div>
    {plan && <p className="checkout-rule" data-testid="pickup-rule-summary"><strong>取货规则：</strong>{pickupSummary}</p>}
    <div className="checkout-main">
      <label className="checkout-target">选购目标<select aria-label="选购目标" className="rounded border bg-background text-foreground" value={selected} onChange={e => setSelected(e.target.value)}><option value="">本地区列表第一项</option>{localTargets.map(t => <option key={targetKey(t)} value={targetKey(t)}>{t.storeTitle} · {t.productName}</option>)}</select></label>
      <div className="checkout-actions">
        <div className="action-group" role="group" aria-label="开始选购">
          <Button disabled={locked || !localTargets.length || !plan} onClick={() => void command("arm")}>{status?.armed ? "已启用，等待有货" : "启用有货自动选购"}</Button>
          <Button variant="outline" className="manual-start" disabled={pending || !!status?.busy || !!status?.stopping || !localTargets.length || !plan || foreign} title="不等监控：按所选目标的型号直接开始选购；指定门店不能取货时改选同城能取货的门店" onClick={() => void command("manual")}>手动开始选购</Button>
        </div>
        <div className="action-group" role="group" aria-label="选购过程">
          <Button size="sm" variant="outline" disabled={pending || !!status?.busy || !!status?.stopping || !localTargets.length || !plan} onClick={() => void command("rehearse")}>测试演练</Button>
          <Button size="sm" variant="outline" disabled={pending || !status?.busy || status.stopping || status.mode === "ready"} onClick={() => void command("continue")}>人工处理后继续</Button>
          <Button size="sm" variant="outline" className="stop-checkout" hidden={!status?.busy && !status?.armed && !status?.stopping} disabled={pending} onClick={() => void command("stop")}>停止选购</Button>
        </div>
        <Button size="sm" variant="ghost" className="action-login" disabled={locked || foreign} title="打开 Apple 账号页，在弹出的浏览器窗口登录；登录确认后自动结束，不加购" onClick={() => void command("login")}>{status?.loginTask ? "等待登录…" : "登录 Apple 账户"}</Button>
      </div>
    </div>
    <p className="checkout-note" data-testid="checkout-session">账号登录：{status?.session || "尚未检查。开售前请先点「登录 Apple 账户」登录好，结账时少一步。"}</p>
    {(!plan || !localTargets.length || locked) && <p className="checkout-note" data-testid="checkout-disabled-reason">{pending ? "正在处理，请稍候…" : status?.stopping ? "正在停止，完成后可重新启用。" : needsAttention ? "当前选购已暂停，新库存不会另开订单。请按下方原因处理后继续；若官网窗口已关闭，请先停止选购，再重新启用。" : status?.busy ? "选购正在执行；如需重新启用，请先停止选购。" : status?.armed ? "自动选购已启用；修改方案或重新启用前，请先停止选购。" : !plan ? "选购组件尚未就绪；连接失败时请重启软件后重试。" : "请先在左侧添加至少一个监控目标（与选购地区一致）。"}</p>}
    <Dialog open={confirmation !== null} onOpenChange={value => { if (!value) setConfirmation(null); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{confirmation === "arm" ? "确认启用自动下单" : confirmation === "manual" ? "确认手动开始并自动下单" : "确认已核对 Apple 订单"}</DialogTitle>
          <DialogDescription>{confirmation === "arm" ? "正式选购将按已保存的配置、总预算和付款方案，自动点击 Apple 的「立即下单」并接受该订单的商店条款，创建真实订单。付款、授信和签约仍由你本人完成。测试演练永不提交。" : confirmation === "manual" ? "将不等监控，按所选目标的型号直接开始选购；指定门店不能取货时改选同城能取货的门店。到订单核对页后自动点击「立即下单」创建真实订单。付款、授信和签约仍由你本人完成。" : "请先在 Apple 官网查看订单，确认上次下单结果，并确定不会重复购买。完成核对后才能开始新的选购。"}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => setConfirmation(null)}>取消</Button>
          <Button disabled={confirmation === "manual" ? pending || !!status?.busy || !!status?.stopping : locked} onClick={() => { if (confirmation) void command(confirmation, true); }}>{confirmation === "arm" ? "确认启用并授权下单" : confirmation === "manual" ? "确认开始并授权下单" : "已核对，允许新选购"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    {status?.intervention && <div className="mt-3 rounded-xl border border-amber-400 bg-amber-50 p-4 text-amber-950 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200" role="alert" aria-live="assertive" data-testid="checkout-intervention"><strong>{status.intervention.title}</strong><p className="mt-1">{status.intervention.body}</p><p className="mt-2 text-xs">软件同时向已配置的 Bark 发送提醒；若未收到，请检查手机提醒设置。请在 Apple 官网本人处理，提醒不代表已付款或已锁单。</p></div>}
    <p className="checkout-note" hidden={!status?.busy && !status?.armed && !status?.stopping && !!status && !status.error} role="status">{status?.stopping ? "正在停止，等待当前操作返回…" : `${status?.armed && !status.busy ? "已启用 · " : ""}${status?.reason || "正在连接选购组件…"}`}{status?.elapsed ? ` · ${status.elapsed.toFixed(1)} 秒` : ""}</p>
    {(error || status?.error) && <p className="mt-1 text-destructive" role="alert">{error || status?.error}</p>}
    {status?.profileRequired && <div className="mt-2 rounded border border-amber-500 p-3" role="alert"><strong>请先填写并保存联系与收货资料。</strong><Button variant="outline" size="sm" disabled={locked} onClick={() => !(expanded ?? open) && onSettings ? onSettings() : setOpen(true)}>填写收货资料</Button></div>}
    {status?.orderNeedsReview && <div className="mt-2 rounded border border-amber-500 p-2">已有下单记录。先在 Apple 核对订单，停止选购后才能清除此保护。<Button variant="outline" size="sm" disabled={locked} onClick={() => void command("acknowledge_order")}>已核对订单，允许新选购</Button></div>}
    <ConfigModal id="purchase-plan" open={showPlan} title="选购方案设置" description="改完点底部「保存设置」；关闭窗口会保留未保存的输入。" closeLabel="关闭选购方案" onClose={closePlan}
      footer={plan && <><Button variant="ghost" size="sm" disabled={locked} onClick={() => void command("import")}>导入原抢先购方案</Button><Button size="sm" disabled={locked} onClick={() => void command("configure")}>保存设置</Button></>}>
    <p className="section-note">测试演练无需开启监测或提前加购；按所选目标在官网验证，始终停在提交前。正式选购先开启监测，再启用自动选购。监控出问题时可点「手动开始选购」：不等监控，按所选目标的型号直接开始；指定门店不能取货时改选同城能取货的门店，是否自动下单按方案设置。</p>
    {plan && <div className="plan-fields grid gap-5">
      <label className="font-semibold">选购国家／地区<select aria-label="选购国家／地区" className="block rounded border bg-background text-foreground p-2 mt-2" disabled={locked} value={locale} onChange={e=>{setSelected('');edit({checkout_locale:e.target.value,max_price:0,auto_submit_order:false,financing:e.target.value==='zh_CN'?'full':'manual',payment:e.target.value==='zh_CN'?'支付宝':'官网手动选择',installment_option:'',installment_months:0});}}>{checkoutRegions.map(r=><option key={r.locale} value={r.locale}>{r.title} · {r.currency}</option>)}</select></label>
      {foreign&&<p role="status" className="rounded-xl bg-amber-50 p-3 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">国际选购候选适配 · 尚未完成当地官网全流程验收。仅处理本地区监控目标；银行卡、付款、分期及最终下单由本人完成，不承诺待支付锁单。请重新设置当地货币预算及资料。</p>}
      <ContactProfile key={locale} locale={locale} locked={locked} beforeSave={async()=>{await invoke("checkout_command",{op:"configure",payload:{plan}});dirty.current=false;}} />
      <h3>预算与保障</h3>
      <div className="plan-controls flex flex-wrap items-end gap-3">
        <label>总预算（含保障） · {local.currency}<Input type="number" min={1} max={foreign?100000000:100000} disabled={locked} value={plan.max_price} onChange={e => edit({ max_price: Number(e.target.value) })} /></label>
        <label>数量<select className="block rounded border bg-background text-foreground p-2" disabled={locked} value={plan.quantity} onChange={e => edit({quantity:Number(e.target.value)})}><option value={1}>1</option><option value={2}>2</option></select></label>
        <label>AppleCare+<select className="block rounded border bg-background text-foreground p-2" disabled={locked} value={plan.applecare} onChange={e => edit({applecare:e.target.value})}><option value="none">不添加</option><option value="add">添加（计入预算）</option><option value="manual">官网手动选择</option></select></label>
      </div>
      <h3>付款方案</h3>
      {foreign&&<p className="section-note">付款和分期以当地 Apple 官网当前显示为准，进入后由本人选择；不会自动填银行卡、签约或提交订单。</p>}
      {locked && <p className="section-note">选购已启用或正在执行，请先点击「停止选购」再修改付款方案。</p>}
      {!foreign&&<div role="group" aria-label="付款计划" className="flex flex-wrap gap-2">
        <Button variant={plan.financing === "full" ? "default" : "outline"} aria-pressed={plan.financing === "full"} disabled={locked} onClick={() => edit({financing:"full"})}>全款</Button>
        <Button variant={plan.financing === "official" ? "default" : "outline"} aria-pressed={plan.financing === "official"} disabled={locked} onClick={() => edit({financing:"official"})}>分期</Button>
        <Button variant={plan.financing === "manual" ? "default" : "outline"} aria-pressed={plan.financing === "manual"} disabled={locked} onClick={() => edit({financing:"manual",auto_submit_order:false})}>官网手选</Button>
      </div>}
      {!foreign&&plan.financing === "full" &&
      <div className="plan-controls flex flex-wrap items-end gap-3">
        <label>全款渠道<select className="block rounded border bg-background text-foreground p-2" disabled={locked} value={plan.payment} onChange={e => edit({payment:e.target.value})}>{["支付宝","微信支付","Apple Pay","官网手动选择"].map(v => <option key={v}>{v}</option>)}</select></label>
      </div>}
      {!foreign&&plan.financing === "official" && <fieldset disabled={locked} className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        <legend className="mb-2 font-semibold">分期付款方案 · 点击期数即可设置</legend>
        {installmentPlans.map(p => <div key={p.provider} className={`rounded-xl border bg-card text-card-foreground p-4 ${plan.installment_option === p.provider ? "border-primary ring-1 ring-primary" : "border-border"}`}>
          <strong>{p.provider}</strong>
          <div className="mt-3 flex flex-wrap gap-2">{p.months.map(n => <button type="button" key={n} disabled={locked}
            aria-pressed={plan.installment_option === p.provider && plan.installment_months === n}
            className={`rounded-lg border px-3 py-2 disabled:opacity-50 ${plan.installment_option === p.provider && plan.installment_months === n ? "border-primary bg-primary text-primary-foreground" : "bg-background text-foreground hover:bg-accent"}`}
            onClick={() => edit({installment_option:p.provider,installment_months:n})}>{n} 期</button>)}</div>
        </div>)}
        <p className="text-xs text-muted-foreground sm:col-span-2 lg:col-span-3">{plan.installment_option && plan.installment_months ? `已选：${plan.installment_option} · ${plan.installment_months} 期。` : "请选择渠道下的期数。"}以上来自本次官网页面；选购时再次核验渠道、期数与零费率，不可用时保留现场，不替换方案。更多银行请使用官网手选。</p>
      </fieldset>}
      <h3>下单方式</h3>
      <label className="submission-choice flex items-center gap-3"><input type="checkbox" disabled={locked || foreign || plan.financing === "manual" || plan.payment === "官网手动选择"} checked={!!plan.auto_submit_order} onChange={e => edit({auto_submit_order:e.target.checked})} /><span><strong>自动下单，等待本人支付</strong><small>{foreign?'本地区自动提交未开放，避免触发银行卡扣款。':'启用选购时需再次确认；关闭时停在最终提交前。'}</small></span></label>
      <h3>取货与购物袋</h3>
      <label>取货日期策略<select aria-label="取货日期策略" className="block rounded border bg-background text-foreground p-2" disabled={locked} value={datePolicy} onChange={e => edit({pickup_date_policy:e.target.value})}><option value="by_model">按型号 · Duo 指定日期，其他最早可取</option><option value="earliest">所有型号 · 最早可取</option><option value="specified">所有型号 · 指定日期</option></select></label>
      {datePolicy === "earliest" ? <p className="section-note">按官网当前最早可取日期选择最早可用签到时段；该日明确无时段时再试下一日期。已保存的指定日期仍保留，切回按型号或指定日期即可查看。</p> : <>
        <p className="section-note">{datePolicy === "by_model" ? "以下日期只约束 Duo，所有容量、颜色和门店均生效；其他型号按官网最早日期及最早可用时段选择。Duo 没有范围内日期时不会改选更早或更晚日期。" : "以下日期约束当前地区所有型号，按先后顺序选择。"}</p>
        <div className="flex flex-wrap items-end gap-3">{[0,1,2].map(i => <label key={i}>{datePolicy === "by_model" ? "Duo 取货日期" : "取货日期"} {i + 1}<Input type="date" disabled={locked} value={plan.pickup_dates[i] || ""} onChange={e => { const ds=[...plan.pickup_dates];ds[i]=e.target.value;edit({pickup_dates:ds.filter(Boolean)}); }} /></label>)}</div>
      </>}
      {foreign && <p className="section-note">国际日期仅按官网可明确核验的日期选择；无法识别时提示本人处理，不猜测当地日期。</p>}
      <label className="flex items-center gap-2"><input type="checkbox" disabled={locked} checked={plan.clear_bag_before_purchase} onChange={e => edit({clear_bag_before_purchase:e.target.checked})} />每次新选购前清空当前 Apple 账号购物袋（包括已有商品）</label>
      {!localTargets.length&&<p role="alert">当前选购地区没有监控目标，请先在左侧添加对应地区的商品和门店。</p>}
      <p className="section-note">{foreign?'到付款或需要人工处理时发送 Bark 提醒；国际版不自动锁单、付款或授信。':'确认待支付状态后发送 Bark 提醒。下单不保证留货；授信、签约和支付由本人完成。'}</p>
    </div>}
    </ConfigModal>
  </section>;
}
