//! Private child-process protocol. Stock detection stays in apw-core.
use serde_json::{Value, json};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, mpsc};
use std::time::Duration;

struct Process {
    child: Child,
    input: ChildStdin,
    output: mpsc::Receiver<String>,
    failed: bool,
    first_reply: bool,
}

#[cfg(test)]
mod tests {
    use super::*;
    use apw_core::{
        catalog::Catalog,
        model::{Availability, Target},
        watcher::TargetState,
    };
    #[test]
    fn shared_python_fixture_matches_real_rust_wire_and_catalog() {
        let expected: Value =
            serde_json::from_str(include_str!("../../checkout/fixtures/apw-hit.json")).unwrap();
        let target: Target = serde_json::from_value(expected["target"].clone()).unwrap();
        let product = Catalog::new()
            .product_by_part(&target.locale, &target.part_number)
            .unwrap();
        let row = TargetState {
            target,
            availability: Availability::InStock,
            last_checked_ms: Some(100000),
            consecutive_failures: 0,
        };
        let mut actual = serde_json::to_value(row).unwrap();
        actual["product"] = serde_json::to_value(product).unwrap();
        assert_eq!(actual, expected);
    }
}
pub struct Checkout {
    executable: PathBuf,
    process: Mutex<Option<Process>>,
    engaged: AtomicBool,
}

/// A checkout denial applies to the same storefront used by inventory polling.
/// Keep this distinct from missing stock or a normal unavailable product URL.
pub fn access_restricted(status: &Value) -> bool {
    status["accessBlocked"] == true
}

#[cfg(test)]
mod access_tests {
    use super::*;

    #[test]
    fn only_explicit_access_denial_stops_monitoring() {
        assert!(access_restricted(
            &json!({"accessBlocked":true, "stage":"BLOCKED"})
        ));
        for stage in ["PRODUCT", "NOT_FOUND", "SOLD_OUT", "LOGIN", "UNKNOWN"] {
            assert!(!access_restricted(
                &json!({"accessBlocked":false, "stage":stage})
            ));
        }
        assert!(!access_restricted(&json!({})));
    }
}

impl Checkout {
    pub fn new(executable: PathBuf) -> Self {
        Self {
            executable,
            process: Mutex::new(None),
            engaged: AtomicBool::new(false),
        }
    }
    pub fn request(&self, payload: Value) -> Result<Value, String> {
        let mut slot = self.process.lock().map_err(|_| "选购通信状态异常")?;
        if slot.is_none() {
            let mut command = Command::new(&self.executable);
            if cfg!(feature = "international-preview") {
                command.env("APW_CHECKOUT_VARIANT", "international");
            }
            #[cfg(target_os = "windows")]
            {
                use std::os::windows::process::CommandExt;
                command.creation_flags(0x08000000); // CREATE_NO_WINDOW; keep stdio IPC.
            }
            let mut child = command
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::null())
                .spawn()
                .map_err(|_| "选购组件无法启动；未执行官网操作")?;
            let input = child.stdin.take().ok_or("选购输入通道不可用")?;
            let mut reader = BufReader::new(child.stdout.take().ok_or("选购输出通道不可用")?);
            let (sender, output) = mpsc::sync_channel(1);
            std::thread::spawn(move || {
                loop {
                    let mut line = String::new();
                    let result = reader.by_ref().take(262145).read_line(&mut line);
                    if result.is_err()
                        || line.is_empty()
                        || line.len() > 262144
                        || !line.ends_with('\n')
                    {
                        break;
                    }
                    if sender.send(line).is_err() {
                        break;
                    }
                }
            });
            *slot = Some(Process {
                child,
                input,
                output,
                failed: false,
                first_reply: true,
            });
        }
        let process = slot.as_mut().ok_or("选购组件不可用")?;
        if process.failed {
            return Err("选购通信已失效；保留选购页面，核对后重启软件".into());
        }
        if process
            .child
            .try_wait()
            .map_err(|_| "选购组件状态不可读")?
            .is_some()
        {
            return Err("选购组件已退出；先核对购物袋，重启软件后再处理".into());
        }
        let mut request = serde_json::to_vec(&payload).map_err(|_| "选购参数无效")?;
        if request.len() > 262_000 {
            return Err("选购请求过大".into());
        }
        request.push(b'\n');
        process
            .input
            .write_all(&request)
            .and_then(|()| process.input.flush())
            .map_err(|_| "选购通信中断；不得重复操作")?;
        // Explicit profile reads may wait for the user's Keychain approval.
        let wait = if process.first_reply || payload["op"] == "read_profile" {
            30
        } else {
            5
        };
        let line = match process.output.recv_timeout(Duration::from_secs(wait)) {
            Ok(line) => line,
            Err(_) => {
                process.failed = true;
                // Cancellation is fire-and-forget on a poisoned protocol;
                // never reuse a late response as another action's receipt.
                let _ = process.input.write_all(b"{\"op\":\"stop\"}\n");
                let _ = process.input.flush();
                return Err("选购通信超时；已请求停止，操作结果需人工核对".into());
            }
        };
        process.first_reply = false;
        let response: Value = serde_json::from_str(&line).map_err(|_| "选购组件未返回有效回执")?;
        if response["ok"] == true {
            let s = &response["value"];
            self.engaged.store(
                s["armed"] == true || s["busy"] == true || s["stopping"] == true,
                Ordering::SeqCst,
            );
            Ok(response["value"].clone())
        } else {
            Err(response["error"]
                .as_str()
                .unwrap_or("选购请求未完成")
                .to_string())
        }
    }
    pub fn engaged(&self) -> bool {
        self.engaged.load(Ordering::SeqCst)
    }
    pub fn stop_if_started(&self) {
        let started = self.process.lock().map(|p| p.is_some()).unwrap_or(false);
        if started {
            let _ = self.request(json!({"op": "stop"}));
        }
    }
}
