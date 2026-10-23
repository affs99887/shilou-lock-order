import { type CloudEvent, type CommunityStatus, formatTime } from "@/lib/types";

/**
 * 顶栏监测状态后面那句「N 秒后查询」。
 *
 * `nextAt` 是上一轮结束时按引擎报的等待秒数算出的下一轮时刻；还没收到第一轮结果时为 null，
 * 这时引擎正在查，显示「正在查询」。倒计时走到 0 也显示「正在查询」：引擎的间隔带随机抖动，
 * 到点后还要等这一轮请求回来。
 */
export function nextQueryText(running: boolean, nextAt: number | null, now: number): string {
  if (!running) return "";
  if (nextAt === null) return "正在查询";
  const seconds = Math.ceil((nextAt - now) / 1000);
  return seconds > 0 ? `${seconds} 秒后查询` : "正在查询";
}

/**
 * 一条云端查询记录在查询日志里的写法。准备查询那条返回 null，不写进日志。
 *
 * 只按后端核对过的计数说话：对方说成功、但门店、型号或时间对不上的结果，后端不会给计数，
 * 这里也就不会写成「暂无现货」。
 */
export function cloudLogLine(event: CloudEvent, storeTitle: string): string | null {
  if (event.origin === "local" && event.stage === "request") return null;
  const where = `云端 · ${storeTitle}`;
  if (event.origin === "local") return `${where}：连接没有完成，这次没拿到库存结果（${event.message || "原因未知"}）`;
  if (event.outcome === "rate_limited") return `${where}：对方正在限流，库存未确认`;
  if (event.outcome === "too_frequent") return `${where}：查询太频繁，对方要求稍后再查，库存未确认`;
  const c = event.counts;
  if (event.outcome !== "checked" || !c || c.total <= 0) return `${where}：这次没拿到能确认的库存结果`;
  if (c.inStock > 0) return `${where}：${c.inStock} 款有货${c.unconfirmed > 0 ? `，${c.unconfirmed} 款未确认` : ""}`;
  if (c.outOfStock === c.total) return `${where}：所选 ${c.total} 款都暂无自提现货`;
  return `${where}：${c.outOfStock > 0 ? `${c.outOfStock} 款暂无自提现货，` : ""}${c.unconfirmed} 款未确认`;
}

/** 顶栏的共享线索状态。没开过也没出过错时不显示。 */
export function communityText(c: CommunityStatus | null): { text: string; title?: string } | null {
  if (!c) return null;
  if (c.error) return { text: "共享线索暂不可用", title: c.error };
  if (c.lastCheckedMs !== null) return { text: `共享线索 ${formatTime(c.lastCheckedMs)} 更新` };
  return c.running ? { text: "共享线索连接中" } : null;
}
