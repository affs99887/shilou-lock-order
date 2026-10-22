//! Background shared hints. Independent of window visibility and Apple polling.
use std::sync::RwLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use apw_core::community::{Client, Matcher, Signal};
use apw_core::model::Target;
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tokio::sync::watch;

use crate::AppState;

#[derive(Clone)]
pub struct ControlState {
    running: bool,
    targets: Vec<Target>,
    generation: u64,
}

#[derive(Clone, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Status {
    pub running: bool,
    pub last_checked_ms: Option<u64>,
    pub last_signal: Option<Signal>,
    pub error: Option<String>,
    pub next_check_in_secs: Option<u64>,
}

pub struct Community {
    control: watch::Sender<ControlState>,
    status: RwLock<Status>,
}

impl Community {
    pub fn new(targets: Vec<Target>) -> (Self, watch::Receiver<ControlState>) {
        let (control, receiver) = watch::channel(ControlState {
            running: false,
            targets,
            generation: 0,
        });
        (
            Self {
                control,
                status: RwLock::new(Status::default()),
            },
            receiver,
        )
    }

    pub fn set_running(&self, running: bool) {
        self.control.send_if_modified(|state| {
            if state.running == running {
                return false;
            }
            state.running = running;
            state.generation += 1;
            true
        });
    }

    pub fn set_targets(&self, targets: &[Target]) {
        self.control.send_if_modified(|state| {
            if state.targets == targets {
                return false;
            }
            state.targets = targets.to_vec();
            state.generation += 1;
            true
        });
    }

    pub fn snapshot(&self) -> Status {
        self.status
            .read()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }

    fn publish(&self, app: &AppHandle, status: &Status) {
        *self.status.write().unwrap_or_else(|e| e.into_inner()) = status.clone();
        let _ = app.emit("community://status", status);
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

pub async fn run(app: AppHandle, mut control: watch::Receiver<ControlState>, client: Client) {
    let mut matcher = Matcher::default();
    let mut status = Status::default();
    let mut failures: u32 = 0;
    let (notifications, pending) = crate::notification_queue::channel();
    let notification_app = app.clone();
    tauri::async_runtime::spawn(crate::notification_queue::run(
        pending,
        move |notification| {
            let app = notification_app.clone();
            async move {
                if crate::dispatch_notification(&app, notification)
                    .await
                    .is_err()
                {
                    let _ = app.emit(
                        crate::NOTICE_CHANNEL,
                        "共享到货线索提醒发送失败；选购触发不受影响",
                    );
                }
            }
        },
    ));
    loop {
        let config = control.borrow_and_update().clone();
        status.running = config.running;
        if !config.running || config.targets.is_empty() {
            status.next_check_in_secs = None;
            app.state::<AppState>().community.publish(&app, &status);
            if control.changed().await.is_err() {
                return;
            }
            continue;
        }
        let result = tokio::select! {
            biased;
            changed = control.changed() => { if changed.is_err() { return; } continue; }
            result = client.fetch() => result,
        };
        if control.borrow().generation != config.generation {
            continue;
        }
        let now = now_ms();
        let result = result.and_then(|report| {
            matcher.collect(
                &report,
                &config.targets,
                &app.state::<AppState>().catalog,
                now,
            )
        });
        status.last_checked_ms = Some(now);
        match result {
            Ok(signals) => {
                failures = 0;
                status.error = None;
                if let Some(signal) = signals.first() {
                    status.last_signal = Some(signal.clone());
                }
                if !signals.is_empty() {
                    // This private protocol retains the worker's armed, budget,
                    // freshness, reservation and persistent order protections.
                    let handle = app.clone();
                    let hits = signals.clone();
                    let generation = config.generation;
                    let offered = tauri::async_runtime::spawn_blocking(move || {
                        let state = handle.state::<AppState>();
                        let current = state.community.control.borrow();
                        if !current.running || current.generation != generation {
                            return Ok(serde_json::Value::Null);
                        }
                        drop(current);
                        state
                            .checkout
                            .request(serde_json::json!({"op":"offer", "hits":hits}))
                    })
                    .await;
                    if !matches!(offered, Ok(Ok(_))) {
                        let _ = app.emit(
                            crate::NOTICE_CHANNEL,
                            "共享线索已命中，但选购组件未确认接收，请查看选购状态",
                        );
                    }
                    if control.borrow().generation != config.generation {
                        continue;
                    }
                    for signal in signals {
                        let line = format!(
                            "共享到货线索：{} {}（汇总记录，官网库存待确认）",
                            signal.target.store_title, signal.target.product_name
                        );
                        let _ = app.emit(crate::NOTICE_CHANNEL, &line);
                        let notification =
                            apw_core::notify::Notification::new("有货线索 · 社区上报", line);
                        tokio::select! {
                            biased;
                            changed = control.changed() => { if changed.is_err() { return; } break; }
                            _ = notifications.send(notification) => {}
                        }
                    }
                }
            }
            Err(error) => {
                failures = failures.saturating_add(1);
                status.error = Some(error);
            }
        }
        if control.borrow().generation != config.generation {
            continue;
        }
        let delay = apw_core::community::poll_delay(failures);
        status.next_check_in_secs = Some(delay);
        app.state::<AppState>().community.publish(&app, &status);
        tokio::select! {
            changed = control.changed() => { if changed.is_err() { return; } }
            () = tokio::time::sleep(Duration::from_secs(delay)) => {}
        }
    }
}
