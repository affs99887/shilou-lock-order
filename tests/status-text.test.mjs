import assert from "node:assert/strict";
import test from "node:test";
import { loadSource } from "./load-source.mjs";

const { nextQueryText, communityText } = loadSource("src/lib/status-text.ts");

test("next query countdown counts down, then says it is querying", () => {
  assert.equal(nextQueryText(false, 10_000, 0), "");
  assert.equal(nextQueryText(true, null, 0), "正在查询");
  assert.equal(nextQueryText(true, 52_000, 0), "52 秒后查询");
  assert.equal(nextQueryText(true, 52_000, 51_100), "1 秒后查询");
  assert.equal(nextQueryText(true, 52_000, 52_000), "正在查询");
  assert.equal(nextQueryText(true, 52_000, 60_000), "正在查询");
});

test("shared hint status shows errors first, then the last update time", () => {
  assert.equal(communityText(null), null);
  assert.equal(communityText({ running: false, lastCheckedMs: null, lastSignal: null, error: null, nextCheckInSecs: null }), null);
  assert.deepEqual(communityText({ running: true, lastCheckedMs: null, lastSignal: null, error: null, nextCheckInSecs: 60 }), { text: "共享线索连接中" });
  const at = new Date(2026, 9, 23, 8, 33, 5).getTime();
  assert.deepEqual(communityText({ running: true, lastCheckedMs: at, lastSignal: null, error: null, nextCheckInSecs: 60 }), { text: "共享线索 08:33:05 更新" });
  assert.deepEqual(communityText({ running: true, lastCheckedMs: at, lastSignal: null, error: "HTTP 502", nextCheckInSecs: 60 }),
    { text: "共享线索暂不可用", title: "HTTP 502" });
});
