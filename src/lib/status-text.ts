import { type CommunityStatus, formatTime } from "@/lib/types";

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

/** 顶栏的共享线索状态。没开过也没出过错时不显示。 */
export function communityText(c: CommunityStatus | null): { text: string; title?: string } | null {
  if (!c) return null;
  if (c.error) return { text: "共享线索暂不可用", title: c.error };
  if (c.lastCheckedMs !== null) return { text: `共享线索 ${formatTime(c.lastCheckedMs)} 更新` };
  return c.running ? { text: "共享线索连接中" } : null;
}
