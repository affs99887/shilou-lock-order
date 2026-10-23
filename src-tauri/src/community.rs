//! Background shared hints. Independent of window visibility and Apple polling.
use std::collections::BTreeMap;
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

/// A successful IPC exchange is not proof that this hint started checkout.
/// Only the per-request receipt may identify the one selected target.
fn checkout_result(
    response: Option<&serde_json::Value>,
    index: usize,
    count: usize,
) -> &'static str {
    let Some(receipt) = response.and_then(|value| value.get("offer")) else {
        return "自动选购是否启动尚未确认，请查看 App 中的选购状态";
    };
    match receipt["outcome"].as_str() {
        Some("queued") => match receipt["selectedIndex"].as_u64() {
            Some(selected) if selected < count as u64 && selected == index as u64 => {
                "自动选购已受理，等待资料和官网校验；尚未确认下单"
            }
            Some(selected) if selected < count as u64 => {
                "本批线索已选择另一监控目标，本门店未另开选购"
            }
            _ => "自动选购回执无法对应目标，请查看 App 中的选购状态",
        },
        Some("paused") => {
            "自动选购未启动：上一任务已暂停；请核对购物袋或订单，继续原任务，或停止后重新启用"
        }
        Some("not_armed") => "自动选购未启动：尚未启用，请在 App 中启用有货自动选购",
        Some("busy") => "自动选购未启动：已有选购任务正在执行",
        Some("review") => "自动选购未启动：上一任务停在订单核对页，请先处理",
        Some("stopping") => "自动选购未启动：正在停止上一任务",
        Some("order_review") => "自动选购未启动：已有未核对的下单请求，请先检查 Apple 订单",
        Some("blocked") => "自动选购未启动：官网拒绝访问，请先处理当前选购任务",
        Some("activating") => "自动选购未启动：启用所需的资料仍在校验",
        Some("profile_required") => "自动选购未启动：联系资料需要补充，请查看选购方案",
        Some("cooldown") => "自动选购未启动：该目标仍在短暂等待期",
        Some("ineligible") => "自动选购未启动：线索过期或未通过方案检查，具体原因见 App 选购状态",
        Some("empty") => "自动选购未启动：没有可用的选购目标",
        Some("queue_full") => "自动选购未启动：选购后台暂忙，请查看 App 选购状态",
        _ => "自动选购是否启动尚未确认，请查看 App 中的选购状态",
    }
}

/// 只有回执明确写着「本批选中了这一条」，才算这条线索被自动选购接下。
fn accepted(response: Option<&serde_json::Value>, index: usize, count: usize) -> bool {
    response
        .and_then(|value| value.get("offer"))
        .is_some_and(|receipt| {
            receipt["outcome"].as_str() == Some("queued")
                && receipt["selectedIndex"]
                    .as_u64()
                    .is_some_and(|selected| selected < count as u64 && selected == index as u64)
        })
}

pub async fn run(app: AppHandle, mut control: watch::Receiver<ControlState>, client: Client) {
    let mut matcher = Matcher::default();
    // 每条线索（目标 → 上报时间）第一次出现时才提醒，重试时不重复推送。
    let mut announced: BTreeMap<String, u64> = BTreeMap::new();
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
        announced
            .retain(|_, stamp| now.saturating_sub(*stamp) <= apw_core::community::MAX_AGE_MS * 2);
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
                    // 没被接下的线索下一轮会再次出现；只有第一次出现时才提醒、写日志。
                    let new: Vec<bool> = signals
                        .iter()
                        .map(|s| announced.get(&s.target.key().0) != Some(&s.reported_at_ms))
                        .collect();
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
                    if !matches!(offered, Ok(Ok(_))) && new.iter().any(|n| *n) {
                        let _ = app.emit(
                            crate::NOTICE_CHANNEL,
                            "共享线索已命中，但选购组件未确认接收，请查看选购状态",
                        );
                    }
                    if control.borrow().generation != config.generation {
                        continue;
                    }
                    let response = offered
                        .as_ref()
                        .ok()
                        .and_then(|result| result.as_ref().ok());
                    let count = signals.len();
                    for (index, signal) in signals.into_iter().enumerate() {
                        let taken = accepted(response, index, count);
                        if !taken {
                            // 没被接下（没启用、上一单还在处理等）：线索还新鲜时下一轮再交给选购。
                            matcher.release(&signal);
                        }
                        if !new[index] && !taken {
                            continue;
                        }
                        announced.insert(signal.target.key().0, signal.reported_at_ms);
                        let line = format!(
                            "共享到货线索：{} {}（汇总记录，官网库存待确认）。{}",
                            signal.target.store_title,
                            signal.target.product_name,
                            checkout_result(response, index, count)
                        );
                        let _ = app.emit(crate::NOTICE_CHANNEL, &line);
                        if !new[index] {
                            continue; // 重试后才被接下：写进日志，不再重复推送
                        }
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn only_the_selected_hint_counts_as_taken_so_the_rest_are_retried() {
        let response = json!({"offer":{"outcome":"queued","selectedIndex":1}});
        assert!(accepted(Some(&response), 1, 2));
        assert!(!accepted(Some(&response), 0, 2));
        for outcome in [
            "not_armed",
            "paused",
            "busy",
            "stopping",
            "ineligible",
            "queue_full",
        ] {
            let refused = json!({"offer":{"outcome":outcome,"selectedIndex":null}});
            assert!(!accepted(Some(&refused), 0, 1), "{outcome}");
        }
        assert!(!accepted(None, 0, 1));
        assert!(!accepted(Some(&json!(null)), 0, 1));
        assert!(!accepted(
            Some(&json!({"offer":{"outcome":"queued","selectedIndex":5}})),
            0,
            2
        ));
    }

    #[test]
    fn queued_receipt_identifies_only_the_selected_store() {
        let response: serde_json::Value =
            serde_json::from_str(include_str!("../../checkout/fixtures/offer-receipt.json"))
                .unwrap();
        assert!(checkout_result(Some(&response), 1, 2).contains("已受理"));
        assert!(checkout_result(Some(&response), 0, 2).contains("未另开选购"));
        assert!(!checkout_result(Some(&response), 1, 2).contains("已启动"));
    }

    #[test]
    fn paused_and_disabled_are_distinct_from_accepted() {
        for (outcome, expected) in [
            ("paused", "上一任务已暂停"),
            ("not_armed", "尚未启用"),
            ("order_review", "未核对的下单请求"),
            ("stopping", "正在停止"),
            ("cooldown", "等待期"),
            ("ineligible", "未通过方案检查"),
            ("busy", "正在执行"),
            ("review", "订单核对页"),
            ("blocked", "官网拒绝访问"),
            ("activating", "资料仍在校验"),
            ("profile_required", "联系资料需要补充"),
            ("empty", "没有可用"),
            ("queue_full", "后台暂忙"),
        ] {
            let response = json!({"offer":{"outcome":outcome,"selectedIndex":null}});
            let line = checkout_result(Some(&response), 0, 1);
            assert!(line.contains("未启动"), "{outcome}");
            assert!(line.contains(expected), "{outcome}");
        }
    }

    #[test]
    fn missing_or_malformed_receipts_never_claim_started() {
        let responses = [
            json!(null),
            json!({"armed":true,"busy":true}),
            json!({"offer":{"outcome":"future_status"}}),
            json!({"offer":{"outcome":"queued","selectedIndex":null}}),
            json!({"offer":{"outcome":"queued","selectedIndex":true}}),
            json!({"offer":{"outcome":"queued","selectedIndex":-1}}),
            json!({"offer":{"outcome":"queued","selectedIndex":2}}),
        ];
        for response in &responses {
            assert!(
                checkout_result(Some(response), 0, 2).contains("确认")
                    || checkout_result(Some(response), 0, 2).contains("无法对应")
            );
        }
        assert!(checkout_result(None, 0, 2).contains("尚未确认"));
    }
}
