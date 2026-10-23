//! Bounded, read-only monitor journal. No checkout, cookies, headers or account settings.
use apw_core::{apple::RequestRecord, model::Target, watcher::Event};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicU64, Ordering},
        mpsc::{self, SyncSender},
    },
    time::{SystemTime, UNIX_EPOCH},
};
const FILE_BYTES: u64 = 8 * 1024 * 1024;
const FILES: usize = 8;
const MAX_RECORD: usize = 16 * 1024;
const QUEUE: usize = 256;
fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
#[derive(Clone)]
pub struct Diagnostics {
    tx: SyncSender<Message>,
    dropped: Arc<AtomicU64>,
}
enum Message {
    Record(Vec<u8>),
    Export(PathBuf, SyncSender<Result<Export, String>>),
    History(SyncSender<Result<Vec<RestockRow>, String>>),
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Export {
    pub directory: String,
    pub dropped_records: u64,
    pub write_error: Option<String>,
}
fn text(s: &str) -> String {
    // Never retain URL query strings / proxy credentials in error diagnostics.
    s.split_whitespace()
        .map(|s| {
            if s.contains("://") {
                "[URL已省略]"
            } else {
                s
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(1024)
        .collect()
}
impl Diagnostics {
    pub fn start(dir: PathBuf, app_version: &str) -> io::Result<Self> {
        let journal = Journal::new(dir, FILE_BYTES, FILES)?;
        let (tx, rx) = mpsc::sync_channel(QUEUE);
        let dropped = Arc::new(AtomicU64::new(0));
        let error = Arc::new(Mutex::new(None));
        let result = Self {
            tx,
            dropped: dropped.clone(),
        };
        std::thread::Builder::new()
            .name("monitor-journal".into())
            .spawn(move || {
                let mut journal = journal;
                let mut stats = Stats::default();
                let mut book = match RestockBook::load(journal.dir.join("restocks.json")) {
                    Ok(book) => book,
                    Err(e) => {
                        *error.lock().unwrap_or_else(|e| e.into_inner()) =
                            Some(format!("补货历史读取失败：{e}"));
                        RestockBook::new(journal.dir.join("restocks.json"))
                    }
                };
                while let Ok(message) = rx.recv() {
                    match message {
                        Message::Record(line) => {
                            if let Ok(v) = serde_json::from_slice(&line) {
                                stats.apply(&v);
                                book.observe(&v);
                                if v["lastInCycle"] == true
                                    && let Err(e) = book.save()
                                {
                                    *error.lock().unwrap_or_else(|e| e.into_inner()) =
                                        Some(format!("补货表保存失败：{e}"));
                                }
                            }
                            if let Err(e) = journal.append(&line) {
                                dropped.fetch_add(1, Ordering::Relaxed);
                                *error.lock().unwrap_or_else(|e| e.into_inner()) =
                                    Some(e.to_string());
                            }
                        }
                        Message::History(reply) => {
                            let _ = reply.send(Ok(book.rows.iter().rev().cloned().collect()));
                        }
                        Message::Export(dir, reply) => {
                            stats.tick(now());
                            let missed = dropped.load(Ordering::Relaxed);
                            let err = error.lock().unwrap_or_else(|e| e.into_inner()).clone();
                            let snapshot = stats.summary(missed, err.clone());
                            let response = journal
                                .export(&dir, &snapshot)
                                .and_then(|_| {
                                    fs::write(
                                        dir.join("restocks.json"),
                                        serde_json::to_vec_pretty(&book.rows)?,
                                    )
                                })
                                .map(|_| Export {
                                    directory: dir.to_string_lossy().into(),
                                    dropped_records: missed,
                                    write_error: err,
                                })
                                .map_err(|e| e.to_string());
                            let _ = reply.send(response);
                        }
                    }
                }
            })?;
        result.record(json!({"type":"session","version":app_version,"retentionBytes":FILE_BYTES*FILES as u64,"queueCapacity":QUEUE,"maxRecordBytes":MAX_RECORD}));
        Ok(result)
    }
    fn record(&self, mut value: Value) {
        value["recordedAtMs"] = json!(now());
        let Ok(mut line) = serde_json::to_vec(&value) else {
            self.gap(1);
            return;
        };
        if line.len() + 1 > MAX_RECORD {
            self.gap(1);
            return;
        }
        line.push(b'\n');
        if self.tx.try_send(Message::Record(line)).is_err() {
            self.gap(1);
        }
    }
    pub fn gap(&self, n: u64) {
        self.dropped.fetch_add(n, Ordering::Relaxed);
    }
    pub fn scope(&self, targets: &[Target]) {
        self.record(
            json!({"type":"scope","keys":targets.iter().map(|t|t.key().0).collect::<Vec<_>>()}),
        );
    }
    pub fn request(&self, r: &RequestRecord) {
        self.record(json!({"type":"request","atMs":r.at_ms,"requestKind":r.kind,"status":r.status,"durationMs":r.duration_ms,"outcome":text(&r.outcome),"route":text(&r.route),"locale":r.locale,"store":r.store,"parts":r.parts,"transport":r.transport}));
    }
    pub fn cloud_event(&self, event: Value) {
        self.record(json!({"type":"cloudTrace","event":event}));
    }
    pub fn inventory_source(&self, mode: &str, mut status: Value) {
        if let Some(object) = status.as_object_mut() {
            object.remove("events");
        }
        self.record(json!({"type":"inventorySource","mode":mode,"cloudStatus":status}));
    }
    pub fn event(&self, event: &Event, cloud: bool) {
        match event {
            Event::RunStateChanged { running } => {
                self.record(json!({"type":"runState","running":running}))
            }
            Event::CycleComplete {
                snapshot,
                healthy,
                next_check_in_secs,
                paced,
            } => {
                self.scope(
                    &snapshot
                        .iter()
                        .map(|s| s.target.clone())
                        .collect::<Vec<_>>(),
                );
                let cycle = now();
                self.record(json!({"type":"cycle","cycleId":cycle,"healthy":healthy,"targets":snapshot.len(),"known":snapshot.iter().filter(|s|!s.availability.is_unknown()).count(),"nextCheckInSecs":next_check_in_secs,"nextCheckAtMs":cycle+next_check_in_secs*1000,"paced":paced}));
                for (i, s) in snapshot.iter().enumerate() {
                    self.record(json!({"type":"observation","cycleId":cycle,"key":s.target.key().0,"checkedAtMs":s.last_checked_ms,"known":!s.availability.is_unknown(),"availability":s.availability.label(),"reason":if cloud {None} else {s.availability.describe_reason().map(|s|text(&s))},"detailSource":if cloud {"cloudTrace"} else {"local"},"consecutiveFailures":s.consecutive_failures,"storeTitle":s.target.store_title,"productName":s.target.product_name,"queryAtMs":s.last_checked_ms,"lastInCycle":i+1==snapshot.len()}));
                }
            }
            Event::Trouble { reason, advice } if !cloud => {
                self.record(json!({"type":"trouble","reason":text(reason),"advice":advice}))
            }
            // Complete snapshots include unchanged successes; lossy StateChanged is never the history source.
            _ => {}
        }
    }
    pub fn history(&self) -> Result<Vec<RestockRow>, String> {
        let (tx, rx) = mpsc::sync_channel(1);
        self.tx
            .send(Message::History(tx))
            .map_err(|_| "日志线程已停止")?;
        rx.recv().map_err(|_| "补货记录读取失败")?
    }
    pub fn export(&self, directory: PathBuf) -> Result<Export, String> {
        let (tx, rx) = mpsc::sync_channel(1);
        // Called on spawn_blocking; this barrier includes all preceding queued records.
        self.tx
            .send(Message::Export(directory, tx))
            .map_err(|_| "日志线程已停止".to_string())?;
        rx.recv().map_err(|_| "日志导出任务已停止".to_string())?
    }
}
struct Journal {
    dir: PathBuf,
    file: File,
    bytes: u64,
    limit: u64,
    files: usize,
}
impl Journal {
    fn new(dir: PathBuf, limit: u64, files: usize) -> io::Result<Self> {
        fs::create_dir_all(&dir)?;
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(dir.join("monitor.ndjson"))?;
        let bytes = file.metadata()?.len();
        Ok(Self {
            dir,
            file,
            bytes,
            limit,
            files,
        })
    }
    fn path(&self, i: usize) -> PathBuf {
        if i == 0 {
            self.dir.join("monitor.ndjson")
        } else {
            self.dir.join(format!("monitor.{i}.ndjson"))
        }
    }
    fn append(&mut self, line: &[u8]) -> io::Result<()> {
        if self.bytes + line.len() as u64 > self.limit {
            self.file.flush()?;
            let oldest = self.path(self.files - 1);
            if oldest.exists() {
                fs::remove_file(oldest)?;
            }
            for i in (0..self.files - 1).rev() {
                let from = self.path(i);
                if from.exists() {
                    fs::rename(from, self.path(i + 1))?;
                }
            }
            self.file = OpenOptions::new()
                .create(true)
                .append(true)
                .open(self.path(0))?;
            self.bytes = 0;
        }
        self.file.write_all(line)?;
        self.file.flush()?;
        self.bytes += line.len() as u64;
        Ok(())
    }
    fn export(&mut self, dir: &Path, summary: &Value) -> io::Result<()> {
        self.file.flush()?;
        fs::create_dir(dir)?;
        for i in 0..self.files {
            let p = self.path(i);
            if p.exists() {
                fs::copy(&p, dir.join(p.file_name().unwrap()))?;
            }
        }
        fs::write(
            dir.join("analysis.json"),
            serde_json::to_vec_pretty(summary)?,
        )?;
        let coverage = summary["freshnessCoverage"]
            .as_array()
            .map(|a| {
                a.iter()
                    .map(|v| {
                        v.as_f64()
                            .map(|f| format!("{:.2}%", f * 100.0))
                            .unwrap_or("尚无数据".into())
                    })
                    .collect::<Vec<_>>()
                    .join(" / ")
            })
            .unwrap_or_default();
        fs::write(
            dir.join("分析.txt"),
            format!(
                "本次程序运行累计分析\n15 / 30 / 60秒新鲜信息覆盖率：{coverage}\n库存请求：{} 次；HTTP200：{} 次；HTTP541：{} 次\n完整有效轮次：{} / {}\n最长有效观测间隔（含首尾）：{} 秒\n记录缺口：{} 条\n写盘异常：{}\n\n暂停不计入覆盖率分母；更改目标重算覆盖率，其他计数从本次启动累计。该覆盖率不是真实抢购成功率。出现记录缺口或写盘异常时统计不完整。重启前历史仍在轮转文件中，本分析不回放历史文件。\n",
                summary["pickupRequests"],
                summary["pickupHttp200"],
                summary["pickupHttp541"],
                summary["healthyCycles"],
                summary["cycles"],
                summary["maxObservationGapMs"].as_u64().unwrap_or(0) / 1000,
                summary["droppedRecords"],
                summary["writeError"]
            ),
        )?;
        fs::write(
            dir.join("说明.txt"),
            "monitor*.ndjson 按逐行JSON持续记录实际请求和每轮全部目标（失败不是无货）。文件编号越大越旧，monitor.ndjson最新。analysis.json为本次程序启动以来的内存累计统计，轮转可能使旧明细早于统计起点丢失；重启后累计重新开始。覆盖率为运行时段内，每个目标最后成功观测仍处于15/30/60秒窗口的时间占比，不等于真实抢购成功率。暂停不计入分母。scope变化会重置覆盖率统计。droppedRecords大于0或writeError非空时分析不完整；磁盘明细最多64MiB，满后自动删除最旧段。未记录账号、Cookie、请求头或结账信息。导出为日志队列中已接收记录的一致性快照，不会停止监控；仍在进行或尚未进入队列的请求可能在下一次导出中出现。\n",
        )?;
        Ok(())
    }
}
#[derive(Default)]
struct Stats {
    start: u64,
    scope_start: u64,
    last: u64,
    running: bool,
    targets: BTreeMap<String, Option<u64>>,
    covered: [u64; 3],
    denom: u64,
    max_gap: u64,
    requests: u64,
    http541: u64,
    cycles: u64,
    healthy: u64,
    pickup: u64,
    pickup_ok: u64,
    pickup_541: u64,
}
impl Stats {
    fn tick(&mut self, t: u64) {
        let t = t.max(self.last);
        if self.running && self.last > 0 {
            let delta = t - self.last;
            self.denom = self
                .denom
                .saturating_add(delta.saturating_mul(self.targets.len() as u64));
            for last in self.targets.values() {
                if let Some(s) = last {
                    for (i, w) in [15_000, 30_000, 60_000].iter().enumerate() {
                        self.covered[i] =
                            self.covered[i].saturating_add(t.min(s + w).saturating_sub(self.last));
                    }
                }
                self.max_gap = self
                    .max_gap
                    .max(t.saturating_sub(last.unwrap_or(self.start)));
            }
        }
        self.last = t;
    }
    fn apply(&mut self, v: &Value) {
        let at = v["recordedAtMs"].as_u64().unwrap_or_else(now);
        self.tick(at);
        match v["type"].as_str().unwrap_or("") {
            "scope" => {
                let keys: BTreeMap<_, _> = v["keys"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|k| k.as_str().map(|s| (s.to_owned(), None)))
                    .collect();
                if self.targets.keys().ne(keys.keys()) {
                    self.targets = keys;
                    self.covered = [0; 3];
                    self.denom = 0;
                    self.max_gap = 0;
                    self.start = at;
                    self.scope_start = at;
                }
            }
            "runState" => {
                self.running = v["running"].as_bool().unwrap_or(false);
                if self.running {
                    self.start = at;
                    for s in self.targets.values_mut() {
                        *s = None;
                    }
                }
            }
            "observation" => {
                if v["known"] == true
                    && let Some(s) = v["key"].as_str().and_then(|k| self.targets.get_mut(k))
                {
                    *s = Some(v["checkedAtMs"].as_u64().unwrap_or(at));
                }
            }
            "request" => {
                self.requests += 1;
                if v["status"] == 541 {
                    self.http541 += 1;
                }
                if v["requestKind"] == "pickup" {
                    self.pickup += 1;
                    if v["status"] == 200 {
                        self.pickup_ok += 1;
                    }
                    if v["status"] == 541 {
                        self.pickup_541 += 1;
                    }
                }
            }
            "cycle" => {
                self.cycles += 1;
                if v["healthy"] == true {
                    self.healthy += 1;
                }
            }
            _ => {}
        }
    }
    fn summary(&self, dropped: u64, error: Option<String>) -> Value {
        let coverage: Vec<Option<f64>> = self
            .covered
            .iter()
            .map(|n| {
                if self.denom == 0 {
                    None
                } else {
                    Some(*n as f64 / self.denom as f64)
                }
            })
            .collect();
        json!({"exportedAtMs":now(),"statisticsScope":"currentProcess; coverage since latest target-scope change","coverageStartMs":self.scope_start,"running":self.running,"targets":self.targets.len(),"activeTargetMilliseconds":self.denom,"freshnessWindowsSeconds":[15,30,60],"freshnessCoverage":coverage,"maxObservationGapMs":self.max_gap,"requestsIncludingWarm":self.requests,"pickupRequests":self.pickup,"pickupHttp200":self.pickup_ok,"pickupHttp541":self.pickup_541,"http541":self.http541,"cycles":self.cycles,"healthyCycles":self.healthy,"droppedRecords":dropped,"writeError":error,"complete":dropped==0&&error.is_none(),"retainedDetailBytesLimit":FILE_BYTES*FILES as u64})
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn val(t: u64, kind: &str) -> Value {
        json!({"recordedAtMs":t,"type":kind})
    }
    #[test]
    fn unknown_does_not_refresh_and_pause_excluded() {
        let mut s = Stats::default();
        s.apply(&json!({"recordedAtMs":1000,"type":"scope","keys":["a"]}));
        s.apply(&json!({"recordedAtMs":1000,"type":"runState","running":true}));
        s.apply(&json!({"recordedAtMs":2000,"type":"observation","key":"a","known":true,"checkedAtMs":2000}));
        s.apply(&json!({"recordedAtMs":22000,"type":"observation","key":"a","known":false}));
        s.apply(&json!({"recordedAtMs":42000,"type":"runState","running":false}));
        assert_eq!(s.covered, [15000, 30000, 40000]);
        assert_eq!(s.denom, 41000);
        s.tick(92000);
        assert_eq!(s.denom, 41000);
    }
    #[test]
    fn actual_http_not_local_failures() {
        let mut s = Stats::default();
        s.apply(&json!({"recordedAtMs":1,"type":"request","status":541}));
        for _ in 0..10 {
            s.apply(&val(2, "trouble"));
        }
        assert_eq!(s.http541, 1);
    }
    #[test]
    fn rotation_and_export_are_bounded() {
        let root =
            std::env::temp_dir().join(format!("apw-journal-{}-{}", std::process::id(), now()));
        let mut j = Journal::new(root.join("log"), 10, 3).unwrap();
        for _ in 0..15 {
            j.append(b"12345678\n").unwrap();
        }
        assert_eq!(fs::read_dir(&j.dir).unwrap().count(), 3);
        let out = root.join("export");
        j.export(&out, &json!({"ok":true})).unwrap();
        assert!(out.join("analysis.json").exists());
        assert_eq!(fs::read_dir(&j.dir).unwrap().count(), 3);
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn full_queue_and_oversize_record_are_reported_without_growing_memory() {
        let (tx, _rx) = mpsc::sync_channel(1);
        let d = Diagnostics {
            tx,
            dropped: Arc::new(AtomicU64::new(0)),
        };
        d.record(json!({"type":"fixture"}));
        d.record(json!({"type":"fixture"}));
        d.record(json!({"text":"x".repeat(MAX_RECORD+1)}));
        assert_eq!(d.dropped.load(Ordering::Relaxed), 2);
    }
    #[test]
    fn resume_clears_stale_success_and_scope_change_resets_coverage() {
        let mut s = Stats::default();
        s.apply(&json!({"recordedAtMs":1000,"type":"scope","keys":["a"]}));
        s.apply(&json!({"recordedAtMs":1000,"type":"runState","running":true}));
        s.apply(&json!({"recordedAtMs":2000,"type":"observation","key":"a","known":true,"checkedAtMs":2000}));
        s.apply(&json!({"recordedAtMs":3000,"type":"runState","running":false}));
        s.apply(&json!({"recordedAtMs":5000,"type":"runState","running":true}));
        let covered = s.covered;
        s.tick(6000);
        assert_eq!(s.covered, covered);
        s.apply(&json!({"recordedAtMs":6000,"type":"scope","keys":["b"]}));
        assert_eq!(s.covered, [0; 3]);
        assert_eq!(s.denom, 0);
    }
    #[test]
    fn errors_strip_urls_and_limit_size() {
        let s = text(&format!(
            "error https://user:secret@server/path?token=abc {}",
            "x".repeat(3000)
        ));
        assert!(!s.contains("secret"));
        assert!(s.chars().count() <= 1024);
    }
}

const MAX_RESTOCKS: usize = 500;
const MAX_RESTOCK_BYTES: u64 = 2 * 1024 * 1024;
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RestockRow {
    key: String,
    store_title: String,
    product_name: String,
    found_at_ms: u64,
    last_in_stock_ms: u64,
    ended_at_ms: Option<u64>,
    current_status: String,
    checked_at_ms: Option<u64>,
}
struct RestockBook {
    path: PathBuf,
    rows: std::collections::VecDeque<RestockRow>,
    dirty: bool,
}
impl RestockBook {
    fn new(path: PathBuf) -> Self {
        Self {
            path,
            rows: Default::default(),
            dirty: false,
        }
    }
    fn load(path: PathBuf) -> io::Result<Self> {
        let mut book = Self::new(path);
        if book.path.exists() {
            let parsed = (|| {
                if fs::metadata(&book.path)?.len() > MAX_RESTOCK_BYTES {
                    return Err(io::Error::other("补货历史超过容量上限"));
                }
                let mut rows: std::collections::VecDeque<RestockRow> =
                    serde_json::from_slice(&fs::read(&book.path)?).map_err(io::Error::other)?;
                while rows.len() > MAX_RESTOCKS {
                    rows.pop_front();
                }
                Ok(rows)
            })();
            match parsed {
                Ok(rows) => book.rows = rows,
                Err(e) => {
                    fs::rename(
                        &book.path,
                        book.path.with_extension(format!("corrupt-{}", now())),
                    )?;
                    return Err(e);
                }
            }
            for row in &mut book.rows {
                row.current_status = "未确认".into();
            }
        }
        Ok(book)
    }
    fn observe(&mut self, v: &Value) {
        if v["type"] != "observation" {
            return;
        }
        let Some(key) = v["key"].as_str() else { return };
        let at = v["checkedAtMs"].as_u64().unwrap_or_else(now);
        let status = v["availability"].as_str().unwrap_or("未确认");
        let latest = self.rows.iter().rposition(|r| r.key == key);
        if status == "有货" && latest.is_none_or(|i| self.rows[i].ended_at_ms.is_some()) {
            while self.rows.len() >= MAX_RESTOCKS {
                self.rows.pop_front();
            }
            self.rows.push_back(RestockRow {
                key: key.to_string(),
                store_title: v["storeTitle"]
                    .as_str()
                    .unwrap_or("")
                    .chars()
                    .take(128)
                    .collect(),
                product_name: v["productName"]
                    .as_str()
                    .unwrap_or("")
                    .chars()
                    .take(256)
                    .collect(),
                found_at_ms: at,
                last_in_stock_ms: at,
                ended_at_ms: None,
                current_status: "有货".into(),
                checked_at_ms: Some(at),
            });
            self.dirty = true;
        }
        for row in self.rows.iter_mut().filter(|r| r.key == key) {
            row.current_status = if v["known"] == true {
                status
            } else {
                "未确认"
            }
            .to_owned();
            row.checked_at_ms = v["queryAtMs"].as_u64();
            if row.ended_at_ms.is_none() {
                if status == "有货" {
                    row.last_in_stock_ms = at;
                } else if status == "无货" {
                    row.ended_at_ms = Some(at);
                }
            }
            self.dirty = true;
        }
    }
    fn save(&mut self) -> io::Result<()> {
        if !self.dirty {
            return Ok(());
        }
        let data = serde_json::to_vec(&self.rows)?;
        if data.len() as u64 > MAX_RESTOCK_BYTES {
            return Err(io::Error::other("补货历史超过容量上限"));
        }
        let temp = self.path.with_extension("tmp");
        fs::write(&temp, data)?;
        fs::rename(temp, &self.path)?;
        self.dirty = false;
        Ok(())
    }
}
#[cfg(test)]
mod restock_tests {
    use super::*;
    fn stock(t: u64, s: &str) -> Value {
        json!({"type":"observation","key":"zh_CN|R575|TEST","availability":s,"known":s!="未知","checkedAtMs":t,"queryAtMs":t,"storeTitle":"武汉","productName":"演练型号"})
    }
    #[test]
    fn stock_transitions_are_persistent_and_unknown_does_not_create_false_restock() {
        let p =
            std::env::temp_dir().join(format!("restocks-{}-{}.json", std::process::id(), now()));
        let mut b = RestockBook::new(p.clone());
        b.observe(&stock(1, "有货"));
        b.observe(&stock(2, "有货"));
        b.observe(&stock(3, "未知"));
        b.observe(&stock(4, "有货"));
        assert_eq!(b.rows.len(), 1);
        assert_eq!(b.rows[0].last_in_stock_ms, 4);
        b.observe(&stock(5, "无货"));
        b.observe(&stock(6, "有货"));
        assert_eq!(b.rows.len(), 2);
        assert_eq!(b.rows[0].ended_at_ms, Some(5));
        b.save().unwrap();
        let restored = RestockBook::load(p.clone()).unwrap();
        assert_eq!(restored.rows.len(), 2);
        assert_eq!(restored.rows[1].current_status, "未确认");
        fs::remove_file(p).unwrap();
    }
    #[test]
    fn bounded_restock_history_keeps_latest_500_episodes() {
        let mut b = RestockBook::new(PathBuf::new());
        for t in 0..700 {
            b.observe(&stock(t * 2, "有货"));
            b.observe(&stock(t * 2 + 1, "无货"));
        }
        assert_eq!(b.rows.len(), MAX_RESTOCKS);
        assert_eq!(b.rows.back().unwrap().found_at_ms, 1398);
    }
}
