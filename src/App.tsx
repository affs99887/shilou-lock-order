import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  AlertTriangle,
  Bell,
  Download,
  FileDown,
  History,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Trash2,
  X,
  Radar,
  MapPin,
} from "lucide-react";

import { Combobox } from "@/components/Combobox";
import { BarkSettings } from "@/components/BarkSettings";
import { AutoCheckout } from "@/components/AutoCheckout";
import { ConfigModal } from "@/components/ConfigModal";
import { RestockRecords } from "@/components/RestockRecords";
import { useInventoryPurchase } from "@/lib/inventory-purchase";
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
  exportDiagnostics,
  installUpdate,
  refreshProducts,
  setCategory,
  setMonitorMode,
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
  formatTime,
  isUntrusted,
  type MonitorMode,
  type Product,
  type StatusTone,
  type Target,
  targetKey,
} from "@/lib/types";
import { communityText, nextQueryText } from "@/lib/status-text";
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

function StatusBadge({ availability, source }: { availability: Availability; source: MonitorMode }) {
  const { label, tone, detail } = describeAvailability(availability, source);
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

/** 运行状态。一个会呼吸的点比一行灰字更像「它还活着」；运行时后面跟一句下一轮什么时候查。 */
function RunState({ running, detail }: { running: boolean; detail?: string }) {
  return (
    <span className="text-muted-foreground flex items-center gap-2 text-sm" aria-live="off">
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
      {running ? <span className="text-foreground">监测运行中</span> : "监测已停止"}
      {running && detail && <span>{detail}</span>}
    </span>
  );
}

const SOURCES: { mode: MonitorMode; label: string; title: string }[] = [
  { mode: "cloud", label: "云端", title: "通过 v-ss.me 的网页查询库存，本机网络不向 Apple 查库存，留给选购用。对方限流或查不到时不会自动换回本机。" },
  { mode: "local", label: "本机", title: "用这台电脑的网络直接向 Apple 查库存。" },
];

/** 库存来源切换。切换会先停下监测，之后点「开始监测」或「启用有货自动选购」重新查。 */
function SourceSwitch({ mode, disabled }: { mode: MonitorMode; disabled: boolean }) {
  return (
    <div className="source-switch" role="group" aria-label="库存来源">
      <span>库存来源</span>
      {SOURCES.map(s => (
        <button key={s.mode} type="button" aria-pressed={mode === s.mode} title={s.title}
          disabled={disabled} onClick={() => void setMonitorMode(s.mode)}>{s.label}</button>
      ))}
    </div>
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
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [planOpen, setPlanOpen] = useState(false);
  const [restocksOpen, setRestocksOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const source = ui.settings.monitorMode ?? "cloud";
  // 下一轮查询的时刻：每轮结束时按引擎报的等待秒数换算一次，之后每秒刷新显示的倒计时。
  const [nextAt, setNextAt] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    setNextAt(ui.running && ui.pacing ? Date.now() + ui.pacing.nextCheckInSecs * 1000 : null);
  }, [ui.running, ui.pacing]);
  useEffect(() => {
    if (!ui.running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [ui.running]);
  const community = communityText(ui.community);
  // 日志固定在底部、一直展开：新记录到来时滚到最下面；用户往上翻看旧记录时不打扰，翻回底部后再跟随。
  const logBox = useRef<HTMLDivElement>(null);
  const followLogs = useRef(true);
  useEffect(() => {
    const box = logBox.current;
    if (box && followLogs.current) box.scrollTop = box.scrollHeight;
  }, [ui.logs]);
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

  const blockedHint = "受影响的库存状态为未知。程序会等待冷却结束后再尝试查询。";

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
        <header className="app-toolbar">
          <div className="brand"><span className="brand-symbol"><Radar size={21} /></span><div><h1>拾漏锁单</h1><p>Apple 门店库存监控，非 Apple 官方</p></div></div>
          <div className="toolbar-actions">
            <div className="toolbar-run-state"><RunState running={ui.running} detail={nextQueryText(ui.running, nextAt, now)} /></div>
            {ui.running && ui.pacing?.paced && <span className="toolbar-note">查询预算等待</span>}
            {community && <span className="toolbar-note toolbar-community" title={community.title}>{community.text}</span>}
            <SourceSwitch mode={source} disabled={!ui.ready || ui.switching} />
            <Button variant="ghost" aria-haspopup="dialog" aria-expanded={settingsOpen} aria-controls="notification-settings" onClick={() => setSettingsOpen(!settingsOpen)}>
              <Bell /> 手机提醒
            </Button>
            {ui.running ? (
              <Button className="monitor-toggle" variant="secondary" onClick={() => void stopWatching()}>
                <Pause /> 暂停监测
              </Button>
            ) : (
              <Button className="monitor-toggle" onClick={() => void startWatching()} disabled={ui.rows.length === 0 || ui.switching}>
                <Play /> 开始监测
              </Button>
            )}
          </div>
        </header>
        <main className="app-main">

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

        <AutoCheckout targets={targets} expanded={planOpen} onSettings={() => setPlanOpen(!planOpen)} />

        <div className="monitor-workspace">
          <aside data-testid="target-page" className="target-panel" aria-label="添加监控目标">
            <div className="target-heading">
              <div><h2>添加监控</h2></div>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="icon" aria-label="从 Apple 官网更新当前品类的型号列表" disabled={ui.refreshing} onClick={() => void refreshProducts()}>
                    <RefreshCw className={ui.refreshing ? "animate-spin" : undefined} />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>从 Apple 官网更新当前品类的型号列表。</TooltipContent>
              </Tooltip>
            </div>
            <div className="target-body">
              <div className="target-fields">
                <div className="field-pair">
                  <div className="target-field">
                    <Label>地区</Label>
                    <Combobox className="w-full" options={ui.regions.map(r => ({ value: r.locale, label: r.title }))}
                      value={ui.settings.locale} onChange={locale => { setStoreNumbers([]); resetProductSelection(); void changeLocale(locale); }}
                      placeholder="选择地区" searchPlaceholder="搜索地区…" emptyText="没有匹配的地区" />
                  </div>
                  <div className="target-field">
                    <Label>品类</Label>
                    <Combobox className="w-full" options={ui.categories.map(c => ({ value: c.value, label: c.title }))}
                      value={ui.category} onChange={value => { resetProductSelection(); setCategory(value as Category); }}
                      placeholder="选择品类" searchPlaceholder="搜索品类…" emptyText="没有匹配的品类" disabled={ui.categories.length === 0} />
                  </div>
                </div>
                {ui.category === "iphone" ? <>
                  <div className="target-field">
                    <Label>机型 <span>可多选</span></Label>
                    <MultiCombobox className="w-full" options={iphoneFamilyOptions} values={families} onChange={onFamiliesChange}
                      placeholder="选择机型" searchPlaceholder="搜索机型…" emptyText="没有匹配的机型"
                      countLabel={n => `已选 ${n} 款机型`} disabled={iphoneFamilyOptions.length === 0} />
                  </div>
                  <div className="field-pair">
                    <div className="target-field">
                      <Label>容量 <span>可多选</span></Label>
                      <MultiCombobox className="w-full" options={iphoneCapacityOptions} values={capacities} onChange={onCapacitiesChange}
                        placeholder="选择容量" searchPlaceholder="搜索容量…" emptyText="没有匹配的容量"
                        countLabel={n => `已选 ${n} 种容量`} disabled={families.length === 0 || iphoneCapacityOptions.length === 0} />
                    </div>
                    <div className="target-field">
                      <Label>颜色 <span>可多选</span></Label>
                      <MultiCombobox className="w-full" options={iphoneColorOptions} values={colors} onChange={setColors}
                        placeholder="选择颜色" searchPlaceholder="搜索颜色…" emptyText="没有匹配的颜色"
                        countLabel={n => `已选 ${n} 种颜色`} disabled={capacities.length === 0 || iphoneColorOptions.length === 0} />
                    </div>
                  </div>
                </> : <div className="target-field">
                  <Label>型号</Label>
                  <Combobox className="w-full" options={productOptions} value={partNumber} onChange={setPartNumber}
                    placeholder="选择型号" searchPlaceholder="搜索型号…" emptyText="没有匹配的型号" disabled={productOptions.length === 0} />
                </div>}
                <div className="target-field store-field">
                  <Label><MapPin size={13} /> 自提门店 <span>可多选</span></Label>
                  <MultiCombobox className="w-full" options={storeOptions} values={storeNumbers} onChange={setStoreNumbers}
                    placeholder="选择自提门店" searchPlaceholder="搜索门店…" emptyText="没有匹配的门店"
                    countLabel={n => `已选 ${n} 家门店`} disabled={storeOptions.length === 0} />
                </div>
              </div>
            </div>
            <div className="target-actions">
              <Button className="w-full" variant="secondary" onClick={() => void onAdd()} disabled={!canAdd}><Plus /> 添加</Button>
              <p className="target-selection-note" aria-live="polite">
                {selectedProducts.length > 0 ? <span title={selectedProducts.map(p => p.title).join("、")}>{selectedProducts.length} 个型号 × {storeNumbers.length} 家门店</span> : "支持多选，已添加的目标会自动跳过"}
              </p>
            </div>
          </aside>
          <div data-testid="inventory-page" className="inventory-page">
            <div className="inventory-heading">
              <h2>关注的库存 <span className="target-count">{ui.rows.length}</span></h2>
              <div className="inventory-stats">
                <Stat label="有货" value={summary.inStock} tone="inStock" />
                <Stat label="无货" value={summary.outOfStock} />
                {summary.untrusted > 0 && <Stat label="查询异常" value={summary.untrusted} tone="unknown" />}
                <span className="inventory-note">库存以 Apple 官网为准</span>
              </div>
            </div>
        <section aria-label="监控型号与库存状态" className="inventory-table">
          <ScrollArea className="h-full">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="text-muted-foreground w-24 pl-4 text-xs">状态</TableHead>
                  <TableHead className="text-muted-foreground text-xs">门店</TableHead>
                  <TableHead className="text-muted-foreground text-xs">型号</TableHead>
                  <TableHead className="text-muted-foreground w-24 text-xs">最近检查</TableHead>
                  <TableHead className="w-32"><span className="sr-only">操作</span></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {ui.rows.length === 0 ? (
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={5} className="text-muted-foreground h-28 text-center">
                      <div className="inventory-empty"><Radar size={34} /><strong>{ui.ready ? "从一个心仪的型号开始" : "正在载入…"}</strong><p>{ui.ready ? "在左侧选择型号与门店，添加后即可开始监控。" : "正在读取已保存的监控目标"}</p></div>
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
                        <StatusBadge availability={row.availability} source={source} />
                      </TableCell>
                      <TableCell className="font-medium">{row.target.storeTitle}</TableCell>
                      <TableCell className="text-muted-foreground">
                        {row.target.productName}
                      </TableCell>
                      <TableCell className="text-muted-foreground tabular-nums">{formatTime(row.lastCheckedMs)}</TableCell>
                      <TableCell>
                        <div className="flex items-center justify-end gap-2">
                        {row.availability.kind === "in_stock" && <Button size="sm"
                          disabled={purchase.disabled}
                          title="按已保存的选购方案直接选购此商品，不弹窗确认；方案勾了「自动下单」时到核对页会自动下单。选购已启用或执行中时不可重复启动"
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
          </div>
        </div>

        {purchase.error && <p role="alert" className="text-destructive">{purchase.error}</p>}

        <section id="query-logs" aria-labelledby="query-logs-title" className="query-logs shrink-0">
          <div className="logs-heading">
            <h2 id="query-logs-title">查询日志</h2>
            <span>最新的在最下面，有货的行标绿</span>
            <div className="logs-actions">
              <Button variant="ghost" size="sm" aria-haspopup="dialog" aria-expanded={restocksOpen} aria-controls="restock-records" onClick={() => setRestocksOpen(true)}>
                <History /> 补货记录
              </Button>
              <Button variant="ghost" size="sm" disabled={exporting} title="把查询记录导出到「下载」文件夹，方便排查问题；不含账号、Cookie 或结账资料"
                onClick={() => { setExporting(true); void exportDiagnostics().finally(() => setExporting(false)); }}>
                <FileDown /> {exporting ? "正在导出…" : "导出诊断记录"}
              </Button>
            </div>
          </div>
          <div ref={logBox} role="log" className="logs-body font-mono select-text"
            onScroll={e => { const box = e.currentTarget; followLogs.current = box.scrollHeight - box.scrollTop - box.clientHeight < 24; }}>
            {ui.logs.length === 0 ? <p className="text-muted-foreground">开始监控后，查询记录会显示在这里。</p> : ui.logs.map((line, i) => <LogLine key={i} line={line} />)}
          </div>
        </section>
        <ConfigModal id="restock-records" open={restocksOpen} title="补货记录" description="监测期间自动记下每次补货：什么时候开始有货、最后一次确认有货、什么时候确认无货。重启后还在，最多保留 500 条。" closeLabel="关闭补货记录" onClose={() => setRestocksOpen(false)}>
          <RestockRecords open={restocksOpen} refreshKey={ui.rows} />
        </ConfigModal>
        <ConfigModal id="notification-settings" narrow open={settingsOpen} title="手机提醒" description="保存后生效；关闭窗口会保留未保存的输入。" closeLabel="关闭手机提醒" onClose={() => setSettingsOpen(false)}>
          <div data-testid="settings-page" className="settings-grid">
            <BarkSettings saved={ui.settings.barkUrl || ""} />
          </div>
        </ConfigModal>
        </main>
      </div>
    </TooltipProvider>
  );
}
