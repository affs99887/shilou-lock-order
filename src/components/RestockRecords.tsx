import { useEffect, useState } from "react";

import { loadRestockRecords } from "@/lib/store";
import type { RestockRecord } from "@/lib/types";

function when(ms: number | null): string {
  return ms === null ? "—" : new Date(ms).toLocaleString("zh-CN", { hour12: false });
}

/**
 * 补货记录表：监控期间自动记下的每次补货。
 *
 * 弹窗打开时读一次，开着时每轮查询结束（`refreshKey` 变化）再读一次。
 * 「当前状态」是后端按最近一轮查询记的；程序重启后、还没查到之前显示「未确认」。
 */
export function RestockRecords({ open, refreshKey }: { open: boolean; refreshKey: unknown }) {
  const [records, setRecords] = useState<RestockRecord[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    loadRestockRecords()
      .then(rows => { if (!cancelled) { setRecords(rows); setError(""); } })
      .catch(() => { if (!cancelled) setError("补货记录暂时读不出来，请稍后再打开。"); });
    return () => { cancelled = true; };
  }, [open, refreshKey]);

  if (error) return <p role="alert" className="restock-error">{error}</p>;
  if (records === null) return <p className="restock-empty">正在读取…</p>;
  if (records.length === 0) return <p className="restock-empty">还没有补货记录。开始监测后，查到有货会自动记在这里。</p>;
  return (
    <div className="restock-table" role="region" aria-label="补货记录列表" tabIndex={0}>
      <table>
        <thead>
          <tr><th>发现有货</th><th>门店 / 型号</th><th>最后确认有货</th><th>确认无货</th><th>当前状态</th></tr>
        </thead>
        <tbody>
          {records.map(r => (
            <tr key={`${r.key}-${r.foundAtMs}`}>
              <td className="tabular-nums">{when(r.foundAtMs)}</td>
              <td><strong>{r.storeTitle}</strong><span>{r.productName}</span></td>
              <td className="tabular-nums">{when(r.lastInStockMs)}</td>
              <td className="tabular-nums">{r.endedAtMs === null ? "还没确认无货" : when(r.endedAtMs)}</td>
              <td>{r.currentStatus}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
