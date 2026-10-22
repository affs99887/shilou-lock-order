import { type ReactNode, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Download,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Trash2,
  X,
  Radar,
  SlidersHorizontal,
  ListFilter,
  LockKeyhole,
} from "lucide-react";

import { Combobox } from "@/components/Combobox";
import { BarkSettings } from "@/components/BarkSettings";
import { ProxySettings } from "@/components/ProxySettings";
import { AutoCheckout } from "@/components/AutoCheckout";
import { useInventoryPurchase } from "@/lib/inventory-purchase";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { MultiCombobox } from "@/components/MultiCombobox";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  capacityOptions,
  colorOptions,
  familyOptions,
  keepAvailable,
  productsForSelection,
} from "@/lib/product-selection";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

import {
  changeLocale,
  connect,
  dismissUpdate,
  installUpdate,
  refreshProducts,
  setCategory,
  setTargets,
  startWatching,
  stopWatching,
  watcherStore,
} from "@/lib/store";
import {
  type Availability,
  type Category,
  describeAdvice,
  describeAvailability,
  isUntrusted,
  type Product,
  type StatusTone,
  type Target,
  targetKey,
} from "@/lib/types";
import { cn } from "@/lib/utils";

/** 四种展示状态各自的样式。 */
const TONE_CLASS: Record<StatusTone, string> = {
  inStock: "bg-in-stock/15 text-in-stock border-in-stock/30 font-medium",
  outOfStock: "bg-muted text-muted-foreground border-transparent",
  // 「未知」必须和「无货」长得完全不一样。这是整个项目的意义所在：上游把查询
  // 失败显示成「无货」，用户对着一个早已失效的程序空等了大半年。
  unknown: "bg-unknown/15 text-unknown border-unknown/40 font-medium",
  pending: "bg-transparent text-muted-foreground/60 border-dashed",
};

function StatusBadge({ availability }: { availability: Availability }) {
  const { label, tone, detail } = describeAvailability(availability);
  const badge = (
    <Badge variant="outline" className={`min-w-18 justify-center ${TONE_CLASS[tone]}`}>
      {label}
    </Badge>
  );
  if (!detail) return badge;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="cursor-help">{badge}</span>
      </TooltipTrigger>
      <TooltipContent className="max-w-90">{detail}</TooltipContent>
    </Tooltip>
  );
}

/**
 * 一块带标题的分区。「在哪儿加什么」和「怎么跑」分开放，控件不再挤成一排。
 *
 * 传 `onToggle` 就可以折叠：标题行整行可点，折叠后只剩标题和一行摘要。
 */
function Panel({
  title,
  hint,
  open = true,
  onToggle,
  children,
}: {
  title: string;
  hint?: string;
  open?: boolean;
  onToggle?: () => void;
  children: ReactNode;
}) {
  const heading = (
    <>
      <h2 className="shrink-0 text-base font-semibold">{title}</h2>
      {hint !== undefined && (
        <p className="text-muted-foreground min-w-0 flex-1 truncate text-xs" title={hint}>
          {hint}
        </p>
      )}
      {onToggle !== undefined &&
        (open ? (
          <ChevronUp className="text-muted-foreground size-4 shrink-0" />
        ) : (
          <ChevronDown className="text-muted-foreground size-4 shrink-0" />
        ))}
    </>
  );
  return (
    <section className="settings-panel bg-card text-card-foreground rounded-xl border shadow-xs">
      {onToggle !== undefined ? (
        <button
          type="button"
          aria-expanded={open}
          onClick={onToggle}
          className={cn(
            "hover:bg-muted/40 flex w-full items-center gap-3 px-4 py-2 text-left",
            open ? "rounded-t-xl" : "rounded-xl",
          )}
        >
          {heading}
        </button>
      ) : (
        <div className="flex items-center gap-3 px-4 py-2">{heading}</div>
      )}
      {open && <div className="grid gap-2.5 border-t px-4 pt-3 pb-4">{children}</div>}
    </section>
  );
}

/** 运行状态。一个会呼吸的点比一行灰字更像「它还活着」。 */
function RunState({ running }: { running: boolean }) {
  return (
    <span className="text-muted-foreground flex items-center gap-2 text-sm">
      <span className="relative flex size-2">
        {running && (
          <span className="bg-in-stock/60 absolute inline-flex size-full animate-ping rounded-full [animation-duration:2.4s]" />
        )}
        <span
          className={cn(
            "relative inline-flex size-2 rounded-full",
            running ? "bg-in-stock" : "bg-muted-foreground/40",
          )}
        />
      </span>
      {running ? "监测运行中" : "监测已停止"}
    </span>
  );
}

/** 一个统计数字。数字等宽：每轮查询都会变，几个并排时不该来回抖。 */
function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "inStock" | "unknown";
}) {
  return (
    <span className="bg-card inline-flex items-baseline gap-1.5 rounded-md border px-2.5 py-1 text-sm shadow-xs">
      <span className="text-muted-foreground text-xs">{label}</span>
      <span
        className={cn(
          "font-semibold tabular-nums",
          value > 0 && tone === "inStock" && "text-in-stock",
          value > 0 && tone === "unknown" && "text-unknown",
        )}
      >
        {value}
      </span>
    </span>
  );
}

/** 日志行。时间戳弱化，到货那几行用有货色标出来，一屏灰字里一眼能找到。 */
function LogLine({ line }: { line: string }) {
  const match = /^\[(\d{2}:\d{2}:\d{2})\] ?(.*)$/s.exec(line);
  if (!match) return <div className="whitespace-pre-wrap">{line}</div>;
  const [, stamp, text = ""] = match;
  return (
    <div className="whitespace-pre-wrap">
      <span className="text-muted-foreground/60">{stamp}</span>{" "}
      <span className={text.startsWith("有货") ? "text-in-stock font-medium" : "text-muted-foreground"}>
        {text}
      </span>
    </div>
  );
}

export default function App() {
  const ui = useSyncExternalStore(watcherStore.subscribe, watcherStore.getSnapshot);
  const purchase = useInventoryPurchase();

  useEffect(() => {
    void connect();
    // 刻意不在清理函数里断开：这是应用级的单一连接，窗口活着它就该活着。
    // StrictMode 的重复调用由 connect 内部去重。
  }, []);

  // 门店可以一次选好几家：同城门店合并成一次请求之后，多选不增加请求量。
  const [storeNumbers, setStoreNumbers] = useState<string[]>([]);
  // 非 iPhone 品类直接选一个完整型号。
  const [partNumber, setPartNumber] = useState("");
  // iPhone 按机型 → 容量 → 颜色三级选择，三级都可多选：发售当晚常见的是
  // 「这两款、这三个颜色，哪个有货要哪个」，一台一台加太慢。三级组合里目录中
  // 存在的型号全部加入。
  const [families, setFamilies] = useState<string[]>([]);
  const [capacities, setCapacities] = useState<string[]>([]);
  const [colors, setColors] = useState<string[]>([]);
  const [view, setView] = useState<"center" | "targets" | "plan" | "settings">("center");
  const navigation = [
    { id: "center", label: "捡漏中心", icon: Radar, caption: "实时库存与运行记录" },
    { id: "targets", label: "监控目标", icon: ListFilter, caption: "选择门店、机型与配置" },
    { id: "plan", label: "选购方案", icon: LockKeyhole, caption: "预算、分期与下单方式" },
    { id: "settings", label: "手机提醒", icon: SlidersHorizontal, caption: "Bark 通知与代理线路" },
  ] as const;
  const storeOptions = useMemo(
    () => ui.stores.map((s) => ({ value: s.number, label: s.title })),
    [ui.stores],
  );
  // 只列当前品类。四个品类的型号加起来好几百条，全堆进一个下拉框，
  // 想找一台 Mac 得先划过所有 iPhone。
  const productOptions = useMemo(
    () =>
      ui.products
        .filter((p) => p.category === ui.category)
        .map((p) => ({ value: p.partNumber, label: p.title })),
    [ui.products, ui.category],
  );
  const iphoneFamilyOptions = useMemo(
    () => familyOptions(ui.products, ui.category),
    [ui.products, ui.category],
  );
  const iphoneCapacityOptions = useMemo(
    () => capacityOptions(ui.products, ui.category, families),
    [ui.products, ui.category, families],
  );
  const iphoneColorOptions = useMemo(
    () => colorOptions(ui.products, ui.category, families, capacities),
    [ui.products, ui.category, families, capacities],
  );
  // 点「添加」时会加入的型号。iPhone 是三级选择的组合，其他品类就是选中的那一个。
  const selectedProducts = useMemo<Product[]>(() => {
    if (ui.category === "iphone") {
      return productsForSelection(ui.products, ui.category, families, capacities, colors);
    }
    const product = ui.products.find((p) => p.partNumber === partNumber);
    return product ? [product] : [];
  }, [ui.products, ui.category, families, capacities, colors, partNumber]);

  function resetProductSelection() {
    setPartNumber("");
    setFamilies([]);
    setCapacities([]);
    setColors([]);
  }

  // 上级变了，下级只去掉不再可选的项，仍然成立的留着（理由见 keepAvailable）。
  function onFamiliesChange(next: string[]) {
    setFamilies(next);
    const nextCapacities = keepAvailable(
      capacities,
      capacityOptions(ui.products, ui.category, next),
    );
    setCapacities(nextCapacities);
    setColors(
      keepAvailable(colors, colorOptions(ui.products, ui.category, next, nextCapacities)),
    );
  }

  function onCapacitiesChange(next: string[]) {
    setCapacities(next);
    setColors(keepAvailable(colors, colorOptions(ui.products, ui.category, families, next)));
  }

  const targets = useMemo(() => ui.rows.map((r) => r.target), [ui.rows]);

  const summary = useMemo(() => {
    let inStock = 0;
    let outOfStock = 0;
    let untrusted = 0;
    for (const r of ui.rows) {
      if (r.availability.kind === "in_stock") inStock += 1;
      else if (r.availability.kind === "out_of_stock") outOfStock += 1;
      if (isUntrusted(r.availability)) untrusted += 1;
    }
    return { inStock, outOfStock, untrusted };
  }, [ui.rows]);

  const canAdd = storeNumbers.length > 0 && selectedProducts.length > 0;

  // 被拦之后接下来会怎样，取决于代理怎么用。
  const proxyMode = (ui.settings.proxies?.length ?? 0) > 0 ? ui.settings.proxyMode : "mixed";
  const blockedHint =
    proxyMode === "tunnel"
      ? "受影响的库存状态为未知。下一轮会换新的出口 IP 继续查询；一直被拦请换一家代理。"
      : proxyMode === "pool"
        ? "受影响的库存状态为未知。被拦的代理会暂停 5～30 分钟，其余代理继续查询；全部被拦请换一批节点。"
        : "受影响的库存状态为未知。程序会等待冷却结束后再尝试查询。";

  async function onAdd() {
    if (!canAdd) return;

    // 门店 × 型号，每个组合一条目标；已经在列表里的跳过，不重复。
    // 按门店分组排，表格里同一家店的几台机器挨在一起。
    const existing = new Set(targets.map(targetKey));
    const added: Target[] = [];
    for (const number of storeNumbers) {
      const store = ui.stores.find((s) => s.number === number);
      if (!store) continue;
      for (const product of selectedProducts) {
        const next: Target = {
          locale: ui.settings.locale,
          storeNumber: store.number,
          storeTitle: store.title,
          partNumber: product.partNumber,
          productName: product.title,
          ...(product.companionPart ? { companionPart: product.companionPart } : {}),
        };
        const key = targetKey(next);
        if (existing.has(key)) continue;
        existing.add(key);
        added.push(next);
      }
    }
    if (added.length === 0) return;
    await setTargets([...targets, ...added]);
    // 只清型号，门店留着：接着给同一批门店加下一个型号是最常见的操作。
    resetProductSelection();
  }

  async function onRemove(t: Target) {
    await setTargets(targets.filter((x) => targetKey(x) !== targetKey(t)));
  }

  return (
    <TooltipProvider delayDuration={200}>
      <div data-testid="watcher-layout" className="app-shell min-h-screen">
        <aside className="app-sidebar">
          <div className="brand"><span className="brand-symbol"><Radar size={25} /></span><div><strong>拾漏锁单</strong><small>发现好时机，接住每一单</small></div></div>
          <nav aria-label="主导航">{navigation.map(item => <button type="button" key={item.id} aria-current={view === item.id ? "page" : undefined} onClick={() => setView(item.id)}><item.icon size={19}/><span>{item.label}</span></button>)}</nav>
          <div className="sidebar-foot"><RunState running={ui.running}/><p>独立选购工具 · 非 Apple 官方</p></div>
        </aside>
        <main className="app-main">
        <header className="app-toolbar flex items-center justify-between gap-4">
          <div>
            <p className="eyebrow">拾漏锁单 / {navigation.find(n => n.id === view)?.caption}</p>
            <h1 className="text-2xl font-semibold tracking-tight">{navigation.find(n => n.id === view)?.label}</h1>
          </div>
          <div className="flex items-center gap-4">
            {ui.running ? (
              <Button variant="secondary" onClick={() => void stopWatching()}>
                <Pause /> 暂停监测
              </Button>
            ) : (
              <Button onClick={() => void startWatching()} disabled={ui.rows.length === 0}>
                <Play /> 开始监测
              </Button>
            )}
          </div>
        </header>

        <div hidden={view !== "center"} className="intro-strip">
          <div><span className="eyebrow">把准备留在现在</span><h2>补货时，不必从头开始。</h2><p>设好监控目标和选购方案，让等待更简单。</p></div>
          <div className="setup-shortcuts"><button onClick={() => setView("targets")}><b>01</b><span>选目标<small>{targets.length} 项监控配置</small></span></button><button onClick={() => setView("plan")}><b>02</b><span>设方案<small>预算与付款方式</small></span></button><button onClick={() => setView("settings")}><b>03</b><span>收提醒<small>设置 Bark 推送</small></span></button></div>
        </div>
        <div hidden={view !== "center" && view !== "plan"}>
          <AutoCheckout targets={targets} expanded={view === "plan"} onSettings={() => setView(view === "plan" ? "center" : "plan")} />
        </div>

        {ui.trouble?.advice === "try_another_network" && (
          <div role="status" data-testid="monitor-protection"
            className="border-unknown/30 bg-unknown/5 grid gap-1.5 rounded-lg border px-4 py-3 text-sm">
            <p className="text-unknown font-medium">库存查询受阻</p>
            <p className="break-words">{ui.trouble.reason}</p>
            <p className="text-muted-foreground">{blockedHint}</p>
          </div>
        )}
        {ui.trouble !== null && ui.trouble.advice !== "try_another_network" && (
          // 只有标题是红的。原因和建议各占一行、用正常的字色：三段红字堆在一起，
          // 用户反而分不清哪句是原因、哪句是该做的事。
          <div
            role="alert"
            className="border-destructive/30 bg-destructive/5 border-l-destructive grid gap-1.5 rounded-lg border border-l-4 px-4 py-3 text-sm"
          >
            <p className="text-destructive flex flex-wrap items-center gap-x-2 font-medium">
              <AlertTriangle className="size-4" />
              查询状态提示
              <span className="text-muted-foreground font-normal">
                请按下方提示处理后继续
              </span>
            </p>
            <p className="pl-6">{ui.trouble.reason}</p>
            {ui.trouble.advice !== null && (
              // 用户自己能做的那件事单独拎出来，而且要短：issue #3 里那位对着反复
              // 告警的窗口干等了三个小时，缺的不是解释，是一句「换条网络」。
              <p className="text-muted-foreground pl-6">
                <span className="text-foreground font-medium">建议</span>{" "}
                {describeAdvice(ui.trouble.advice)}
              </p>
            )}
          </div>
        )}

        {ui.update !== null && (
          // 只提示，不自作主张安装。用户可能正等着抢购，被强制重启是灾难。
          <div className="bg-card flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border px-4 py-2.5 text-sm shadow-xs">
            <p className="flex items-center gap-2 font-medium">
              <Download className="size-4" /> 有新版本 {ui.update.version}
            </p>
            <p className="text-muted-foreground">
              当前 {ui.update.currentVersion}，安装后需重启应用生效。
            </p>
            <div className="ml-auto flex items-center gap-2">
              <Button size="sm" disabled={ui.installing} onClick={() => void installUpdate()}>
                {ui.installing ? "正在下载…" : "下载并安装"}
              </Button>
              <Button size="sm" variant="ghost" onClick={dismissUpdate}>
                <X /> 稍后
              </Button>
            </div>
          </div>
        )}

        <div hidden={view !== "targets"} data-testid="target-page">
        <Panel
          title="添加监控目标"
          hint="先选门店，再选配置；支持多选"
        >
          <div className="flex flex-wrap items-end gap-3">
            <div className="grid gap-1.5">
              <Label>地区</Label>
              <Combobox
                className="w-36"
                options={ui.regions.map((r) => ({ value: r.locale, label: r.title }))}
                value={ui.settings.locale}
                onChange={(locale) => {
                  // 换地区后旧的门店和型号都不再适用，清掉待添加的选择。
                  setStoreNumbers([]);
                  resetProductSelection();
                  void changeLocale(locale);
                }}
                placeholder="选择地区"
                searchPlaceholder="搜索地区…"
                emptyText="没有匹配的地区"
              />
            </div>

            <div className="grid gap-1.5">
              <Label>品类</Label>
              <Combobox
                className="w-36"
                options={ui.categories.map((c) => ({ value: c.value, label: c.title }))}
                value={ui.category}
                onChange={(value) => {
                  // 换品类后旧的型号不再在下拉框里，清掉待添加的选择。门店不用清，
                  // 它跟品类无关。
                  resetProductSelection();
                  setCategory(value as Category);
                }}
                placeholder="选择品类"
                searchPlaceholder="搜索品类…"
                emptyText="没有匹配的品类"
                disabled={ui.categories.length === 0}
              />
            </div>

            <div className="grid gap-1.5">
              <Label>门店（可多选）</Label>
              <MultiCombobox
                className="w-64"
                options={storeOptions}
                values={storeNumbers}
                onChange={setStoreNumbers}
                placeholder="选择自提门店"
                searchPlaceholder="搜索门店…"
                emptyText="没有匹配的门店"
                countLabel={(n) => `已选 ${n} 家门店`}
                disabled={storeOptions.length === 0}
              />
            </div>
          </div>

          <div className="flex flex-wrap items-end gap-3">
            {ui.category === "iphone" ? (
              <>
                <div className="grid gap-1.5">
                  <Label>机型（可多选）</Label>
                  <MultiCombobox
                    className="w-48"
                    options={iphoneFamilyOptions}
                    values={families}
                    onChange={onFamiliesChange}
                    placeholder="选择机型"
                    searchPlaceholder="搜索机型…"
                    emptyText="没有匹配的机型"
                    countLabel={(n) => `已选 ${n} 款机型`}
                    disabled={iphoneFamilyOptions.length === 0}
                  />
                </div>

                <div className="grid gap-1.5">
                  <Label>容量（可多选）</Label>
                  <MultiCombobox
                    className="w-36"
                    options={iphoneCapacityOptions}
                    values={capacities}
                    onChange={onCapacitiesChange}
                    placeholder="选择容量"
                    searchPlaceholder="搜索容量…"
                    emptyText="没有匹配的容量"
                    countLabel={(n) => `已选 ${n} 种容量`}
                    disabled={families.length === 0 || iphoneCapacityOptions.length === 0}
                  />
                </div>

                <div className="grid gap-1.5">
                  <Label>颜色（可多选）</Label>
                  <MultiCombobox
                    className="w-40"
                    options={iphoneColorOptions}
                    values={colors}
                    onChange={setColors}
                    placeholder="选择颜色"
                    searchPlaceholder="搜索颜色…"
                    emptyText="没有匹配的颜色"
                    countLabel={(n) => `已选 ${n} 种颜色`}
                    disabled={capacities.length === 0 || iphoneColorOptions.length === 0}
                  />
                </div>
              </>
            ) : (
              <div className="grid gap-1.5">
                <Label>型号</Label>
                <Combobox
                  className="w-96"
                  options={productOptions}
                  value={partNumber}
                  onChange={setPartNumber}
                  placeholder="选择型号"
                  searchPlaceholder="搜索型号…"
                  emptyText="没有匹配的型号"
                  disabled={productOptions.length === 0}
                />
              </div>
            )}

            <Button variant="secondary" onClick={() => void onAdd()} disabled={!canAdd}>
              <Plus /> 添加
            </Button>
            {selectedProducts.length > 1 && (
              // 多选时把要加的规模点明：三级各勾几项之后，组合数并不直观。
              <span
                className="text-muted-foreground pb-2 text-sm"
                title={selectedProducts.map((p) => p.title).join("、")}
              >
                {selectedProducts.length} 个型号 × {storeNumbers.length} 家门店
              </span>
            )}

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="从 Apple 官网更新当前品类的型号列表"
                  disabled={ui.refreshing}
                  onClick={() => void refreshProducts()}
                >
                  <RefreshCw className={ui.refreshing ? "animate-spin" : undefined} />
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                从 Apple 官网更新当前品类的型号列表。新机发布后用这个，不必等程序更新。
              </TooltipContent>
            </Tooltip>
          </div>
        </Panel>
        <p className="section-note">添加后在捡漏中心查看每一项的库存状态。</p>
        <Button variant="outline" onClick={() => setView("center")}>查看已添加的 {targets.length} 项监控</Button>
        </div>
        <div hidden={view !== "settings"} data-testid="settings-page">
        <BarkSettings saved={ui.settings.barkUrl || ""} />
        <ProxySettings settings={ui.settings} />
        </div>
        <div hidden={view !== "center"} data-testid="inventory-page" className="inventory-page">
        <div className="inventory-heading"><div><h2>库存一览</h2><p>型号、门店和库存状态，集中查看。</p></div><Button variant="outline" size="sm" onClick={() => setView("targets")}><Plus/>添加目标</Button></div>
        <div className="inventory-stats flex flex-wrap items-center gap-2">
          <Stat label="监控" value={ui.rows.length} />
          <Stat label="有货" value={summary.inStock} tone="inStock" />
          <Stat label="无货" value={summary.outOfStock} />
          {summary.untrusted > 0 && <Stat label="查询异常" value={summary.untrusted} tone="unknown" />}
          {ui.running && ui.pacing !== null && ui.pacing.paced && (
            <span className="text-muted-foreground ml-auto text-sm">
              查询预算等待
            </span>
          )}
        </div>

        {/* Extra controls must never consume the table's remaining height. Grow
            the page instead, preserving readable rows even with settings open. */}
        <section aria-label="监控型号与库存状态" className="bg-card h-[clamp(20rem,40vh,40rem)] shrink-0 overflow-hidden rounded-xl border shadow-xs">
          <ScrollArea className="h-full">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="text-muted-foreground w-24 pl-4 text-xs">状态</TableHead>
                  <TableHead className="text-muted-foreground text-xs">门店</TableHead>
                  <TableHead className="text-muted-foreground text-xs">型号</TableHead>
                  <TableHead className="w-40" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {ui.rows.length === 0 ? (
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={4} className="text-muted-foreground h-28 text-center">
                      {ui.ready ? "还没有监控目标。前往「监控目标」选好门店和型号，点「添加」。" : "正在载入…"}
                    </TableCell>
                  </TableRow>
                ) : (
                  ui.rows.map((row) => (
                    <TableRow
                      key={targetKey(row.target)}
                      // 有货的行整行铺一层有货色。这一刻是这个程序存在的全部理由，
                      // 不该只靠一枚小徽标。
                      className={cn(
                        "odd:bg-muted/30",
                        row.availability.kind === "in_stock" &&
                          "bg-in-stock/10 odd:bg-in-stock/10 hover:bg-in-stock/15",
                      )}
                    >
                      <TableCell className="pl-4">
                        <StatusBadge availability={row.availability} />
                      </TableCell>
                      <TableCell className="font-medium">{row.target.storeTitle}</TableCell>
                      <TableCell className="text-muted-foreground">
                        {row.target.productName}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center justify-end gap-2">
                        {row.availability.kind === "in_stock" && <Button size="sm"
                          disabled={purchase.disabled}
                          title="按已保存的选购方案选购此商品；选购已启用或执行中时不可重复启动"
                          onClick={() => void purchase.purchase(row.target)}>立即选购</Button>}
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label="删除这条监控"
                          onClick={() => void onRemove(row.target)}
                        >
                          <Trash2 />
                        </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </ScrollArea>
        </section>

        {purchase.error && <p role="alert" className="text-destructive">{purchase.error}</p>}
        <Dialog open={purchase.confirmationTarget !== null} onOpenChange={open => { if (!open) purchase.cancel(); }}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>确认按此目标自动下单</DialogTitle>
              <DialogDescription>{purchase.confirmationTarget?.productName} · {purchase.confirmationTarget?.storeTitle}。将按已保存的预算、取货和付款方案自动选购；到达订单核对页后点击「立即下单」创建真实订单。付款、授信和签约仍由本人完成。</DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" disabled={purchase.pending} onClick={purchase.cancel}>取消</Button>
              <Button disabled={purchase.pending} onClick={purchase.confirm}>确认选购并授权下单</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <div className="inventory-heading log-heading"><h2>最近查询</h2></div>
        <section aria-label="查询日志" className="bg-card h-40 shrink-0 overflow-hidden rounded-xl border shadow-xs">
          <ScrollArea className="h-full">
            <div className="p-3 font-mono text-xs leading-5 select-text">
              {ui.logs.length === 0 ? (
                <p className="text-muted-foreground">日志会显示在这里。</p>
              ) : (
                ui.logs.map((line, i) => <LogLine key={i} line={line} />)
              )}
            </div>
          </ScrollArea>
        </section>
        </div>
        </main>
      </div>
    </TooltipProvider>
  );
}
