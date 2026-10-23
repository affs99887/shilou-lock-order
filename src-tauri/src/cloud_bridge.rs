use apw_core::{
    apple::{ApiError, Fetcher, StoreAvailability},
    model::{Region, Target},
};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, VecDeque},
    sync::{Arc, Mutex},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};
use tokio::sync::oneshot;

#[path = "cloud_log.rs"]
mod cloud_log;

const WINDOW: &str = "vss-cloud";
type Reply = oneshot::Sender<Value>;
#[derive(Clone)]
pub struct CloudFetcher {
    app: AppHandle,
    ready: Arc<std::sync::atomic::AtomicBool>,
    pending: Arc<Mutex<BTreeMap<String, Reply>>>,
    gate: Arc<tokio::sync::Mutex<tokio::time::Instant>>,
    observations: Arc<Mutex<BTreeMap<String, u64>>>,
    status: Arc<Mutex<Value>>,
    log: Arc<Mutex<VecDeque<Value>>>,
    log_sink: Option<Arc<dyn Fn(Value) + Send + Sync>>,
}
pub fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
pub fn targets(mut targets: Vec<Target>) -> Vec<Target> {
    for target in &mut targets {
        target.pickup_location = None;
    }
    targets
}
impl CloudFetcher {
    pub fn new(app: AppHandle) -> Self {
        Self {
            app,
            ready: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            pending: Arc::new(Mutex::new(BTreeMap::new())),
            gate: Arc::new(tokio::sync::Mutex::new(tokio::time::Instant::now())),
            observations: Arc::new(Mutex::new(BTreeMap::new())),
            log: Arc::new(Mutex::new(VecDeque::new())),
            log_sink: None,
            status: Arc::new(Mutex::new(
                json!({"message":"尚未连接","receivedAtMs":null,"serverTimestampMs":null}),
            )),
        }
    }
    pub fn status(&self) -> Value {
        let mut status = self.status.lock().unwrap().clone();
        status["events"] = json!(self.log.lock().unwrap().iter().cloned().collect::<Vec<_>>());
        status
    }
    pub fn with_log_sink(mut self, sink: impl Fn(Value) + Send + Sync + 'static) -> Self {
        self.log_sink = Some(Arc::new(sink));
        self
    }
    fn record(&self, event: Value) {
        *self.status.lock().unwrap() = event.clone();
        {
            let mut log = self.log.lock().unwrap();
            log.push_back(event.clone());
            while log.len() > 80 {
                log.pop_front();
            }
        }
        if let Some(sink) = &self.log_sink {
            sink(event);
        }
    }
    pub fn stamp(&self, target: &Target) -> Option<u64> {
        self.observations
            .lock()
            .unwrap()
            .get(&format!(
                "{}:{}:{}",
                target.locale, target.store_number, target.part_number
            ))
            .copied()
    }
    pub fn cancel(&self) {
        if let Ok(mut next) = self.gate.try_lock() {
            *next = tokio::time::Instant::now() + Duration::from_secs(12);
        }
        self.pending.lock().unwrap().clear();
        self.observations.lock().unwrap().clear();
        if let Some(w) = self.app.get_webview_window(WINDOW) {
            let _ = w.eval("clearInterval(window.__apwWait); window.__apwVssCancel?.()");
        }
    }
    async fn ensure_window(&self) -> Result<(), ApiError> {
        if self.app.get_webview_window(WINDOW).is_some() {
            return Ok(());
        }
        let app = self.app.clone();
        let pending = self.pending.clone();
        let ready = self.ready.clone();
        let (tx, rx) = oneshot::channel();
        self.app
            .run_on_main_thread(move || {
                let result = WebviewWindowBuilder::new(
                    &app,
                    WINDOW,
                    WebviewUrl::External("https://v-ss.me/iphone/stock/".parse().unwrap()),
                )
                .title("VSS 云端连接")
                .visible(false)
                .background_throttling(tauri::utils::config::BackgroundThrottlingPolicy::Disabled)
                .on_page_load(move |_, payload| {
                    ready.store(
                        matches!(payload.event(), tauri::webview::PageLoadEvent::Finished),
                        std::sync::atomic::Ordering::SeqCst,
                    );
                })
                .initialization_script(include_str!("cloud_bridge.js"))
                .on_navigation(move |url| {
                    if url.host_str() == Some("apw-vss.invalid") {
                        if url.as_str().len() < 262_144
                            && let Some((_, raw)) = url.query_pairs().find(|(k, _)| k == "payload")
                            && let Ok(value) = serde_json::from_str::<Value>(&raw)
                            && let Some(nonce) = value["nonce"].as_str()
                            && let Some(reply) = pending.lock().unwrap().remove(nonce)
                        {
                            let _ = reply.send(value);
                        }
                        return false;
                    }
                    url.scheme() == "https"
                        && url.host_str() == Some("v-ss.me")
                        && url.path() == "/iphone/stock/"
                })
                .build()
                .map(|_| ())
                .map_err(|e| e.to_string());
                let _ = tx.send(result);
            })
            .map_err(|e| ApiError::Transport(e.to_string()))?;
        rx.await
            .map_err(|_| ApiError::Transport("云端连接窗口未建立".into()))?
            .map_err(ApiError::Transport)
    }
    async fn query(
        &self,
        region: &'static Region,
        store: &str,
        parts: &[String],
    ) -> Result<StoreAvailability, ApiError> {
        let result = self.query_inner(region, store, parts).await;
        if let Err(error) = &result {
            // A received provider response already carries its own evidence.
            // Do not replace it with ApiError's locally generated wording.
            if self.status.lock().unwrap()["origin"] != "vss" {
                self.record(json!({"origin":"local","stage":"connection_error","store":store,"locale":region.locale,"parts":parts,"atMs":now_ms(),"message":error.to_string()}));
            }
        }
        result
    }
    async fn query_inner(
        &self,
        region: &'static Region,
        store: &str,
        parts: &[String],
    ) -> Result<StoreAvailability, ApiError> {
        let mut next = self.gate.lock().await;
        tokio::time::sleep_until(*next).await;
        *next = tokio::time::Instant::now() + Duration::from_secs(12);
        self.record(json!({"origin":"local","stage":"request","store":store,"locale":region.locale,"parts":parts,"atMs":now_ms(),"message":"准备查询 VSS"}));
        self.ensure_window().await?;
        tokio::time::timeout(Duration::from_secs(20), async {
            while !self.ready.load(std::sync::atomic::Ordering::SeqCst) {
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
        })
        .await
        .map_err(|_| ApiError::Transport("VSS 网页未能加载，库存未确认".into()))?;
        let nonce = format!("{:032x}", rand::random::<u128>());
        let request = json!({"nonce":nonce,"store":store,"region":region.locale,"parts":parts});
        let (tx, rx) = oneshot::channel();
        self.pending.lock().unwrap().clear();
        self.pending.lock().unwrap().insert(nonce.clone(), tx);
        // Readiness is awaited in Rust. Dispatch directly: a hidden WKWebView
        // can suspend JS timers, so no request may depend on setInterval.
        let script = format!("window.__apwVssQuery({request})");
        let window = self
            .app
            .get_webview_window(WINDOW)
            .ok_or_else(|| ApiError::Transport("云端连接窗口已关闭".into()))?;
        *next = tokio::time::Instant::now() + Duration::from_secs(12);
        window
            .eval(&script)
            .map_err(|e| ApiError::Transport(e.to_string()))?;
        let response = tokio::time::timeout(Duration::from_secs(32), rx).await;
        self.pending.lock().unwrap().remove(&nonce);
        *next = tokio::time::Instant::now() + Duration::from_secs(12);
        let value = match response {
            Ok(Ok(value)) => value,
            _ => {
                self.ready.store(false, std::sync::atomic::Ordering::SeqCst);
                let _ = window.destroy();
                return Err(ApiError::Transport(
                    "VSS 云端连接超时，已重置连接，将在下一轮重试".into(),
                ));
            }
        };
        let received = now_ms();
        if let Some(error) = value["error"].as_str() {
            return Err(ApiError::Transport(error.into()));
        }
        let parsed = match value["http"].as_u64() {
            Some(429) => Err(ApiError::RateLimited("VSS 请求受限，稍后再试".into())),
            Some(200) => apw_core::cloud::parse_response(
                &value["data"],
                store,
                region.locale,
                parts,
                received,
            ),
            _ => Err(ApiError::Transport(format!(
                "VSS 服务返回 HTTP {}，库存未确认",
                value["http"]
            ))),
        };
        self.record(cloud_log::classified_response(
            store,
            region.locale,
            parts,
            &value,
            received,
            &parsed,
        ));
        let parsed = parsed?;
        let timestamp = value["data"]["timestamp"].as_u64().unwrap() * 1000;
        for part in parts {
            self.observations
                .lock()
                .unwrap()
                .insert(format!("{}:{store}:{part}", region.locale), timestamp);
        }
        Ok(parsed)
    }
}
impl Fetcher for CloudFetcher {
    async fn pickup_message(
        &self,
        region: &'static Region,
        store: &str,
        parts: &[String],
    ) -> Result<StoreAvailability, ApiError> {
        self.query(region, store, parts).await
    }
    async fn pickup_message_nearby(
        &self,
        _: &'static Region,
        _: &str,
        _: &[String],
    ) -> Result<Vec<StoreAvailability>, ApiError> {
        Err(ApiError::Transport("云端查询需指定门店".into()))
    }
}
