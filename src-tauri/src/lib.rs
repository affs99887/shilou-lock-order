//! Tauri 应用外壳。
//!
//! 这一层只做装配和转译：起引擎、把命令转成引擎消息、把引擎事件转发给前端、
//! 在有货时发提醒。**所有业务判断都在 `apw-core` 里**，这里不许出现任何
//! 「什么算有货」之类的逻辑 —— 一旦让界面层参与判断，那条核心不变量就多了
//! 一处可以被绕开的地方。
//!
//! 提醒也刻意放在这一层而不是前端：托盘模式下窗口是隐藏的，WebView 可能被
//! 系统节流甚至挂起，把「及时提醒」挂在一个会被挂起的执行环境上是不能接受的。

use std::sync::RwLock;
use std::time::Duration;

use apw_core::apple::{AppleClient, ClientConfig};
use apw_core::catalog::Catalog;
use apw_core::config::{Settings, SettingsStore};
use apw_core::model::{Category, Product, REGIONS, Store, Target, region_by_locale};
use apw_core::notify::{Bark, Feishu, Multi, Notification, Notifier};
use apw_core::watcher::{Event, TargetState, Watcher, WatcherConfig};
use serde::Serialize;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, WindowEvent};
use tauri_plugin_updater::UpdaterExt;

mod auto_open;
mod checkout;
mod checkout_hits;
mod community;
mod monitor_policy;
mod notification_queue;

use auto_open::AutoOpenBag;

/// 前端事件通道名。前端用 `listen("watcher://event", ...)` 订阅。
const EVENT_CHANNEL: &str = "watcher://event";
/// 启动过程中的降级说明通道：配置读不出来之类的事必须让用户看见。
const NOTICE_CHANNEL: &str = "watcher://notice";

/// 地区的可序列化形式。
///
/// `model::Region` 的字段都是 `&'static str`，而且界面不需要知道 `base_url`
/// 这类内部细节。
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct RegionDto {
    title: &'static str,
    locale: &'static str,
}

/// 品类的可序列化形式。
///
/// 界面上的品类下拉框由这里驱动，而不是在前端另抄一份常量：抄一份就迟早会有
/// 一边先加了品类、另一边还蒙在鼓里。
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct CategoryDto {
    value: Category,
    title: &'static str,
}

struct AppState {
    checkout: checkout::Checkout,
    watcher: Watcher,
    community: community::Community,
    catalog: Catalog,
    http: reqwest::Client,
    /// 设置的内存副本。写盘失败不该让界面卡住，所以内存副本是权威的展示来源。
    settings: RwLock<Settings>,
    /// 为 `None` 表示配置不可持久化（目录不可写，或上次读取失败已放弃写盘）。
    store: Option<SettingsStore>,
}

impl AppState {
    fn settings_snapshot(&self) -> Settings {
        self.settings
            .read()
            .map(|s| s.clone())
            .unwrap_or_else(|e| e.into_inner().clone())
    }

    /// 更新内存副本并尝试落盘。落盘失败只报错，不回滚内存 ——
    /// 用户的操作已经生效了，没道理因为磁盘问题把界面弹回去。
    fn put_settings(&self, mut next: Settings) -> Result<(), String> {
        monitor_policy::apply(&mut next);
        let mut guard = self.settings.write().unwrap_or_else(|e| e.into_inner());
        *guard = next;
        let to_save = guard.clone();
        drop(guard);

        match &self.store {
            Some(store) => store.save(&to_save).map_err(|e| e.to_string()),
            None => Ok(()),
        }
    }
}

#[tauri::command]
fn list_regions() -> Vec<RegionDto> {
    REGIONS
        .iter()
        .map(|r| RegionDto {
            title: r.title,
            locale: r.locale,
        })
        .collect()
}

#[tauri::command]
fn list_categories() -> Vec<CategoryDto> {
    Category::ALL
        .iter()
        .map(|c| CategoryDto {
            value: *c,
            title: c.title(),
        })
        .collect()
}

#[tauri::command]
fn list_stores(state: tauri::State<'_, AppState>, locale: String) -> Result<Vec<Store>, String> {
    state.catalog.stores(&locale).map_err(|e| e.to_string())
}

#[tauri::command]
fn list_products(
    state: tauri::State<'_, AppState>,
    locale: String,
) -> Result<Vec<Product>, String> {
    state.catalog.products(&locale).map_err(|e| e.to_string())
}

/// 从 Apple 官网抓最新型号，替换该地区该品类的内存副本，返回抓到的型号数。
///
/// `category` 为 `None` 时抓该地区的全部购买页。界面传的是当前选中的品类：
/// 一次只抓那几页，用户想看新出的 Mac 不必等 iPhone、iPad、Watch 一起抓完。
#[tauri::command]
async fn refresh_products(
    state: tauri::State<'_, AppState>,
    locale: String,
    category: Option<Category>,
) -> Result<usize, String> {
    let region = region_by_locale(&locale).ok_or_else(|| format!("认不出地区 {locale}"))?;
    state
        .catalog
        .refresh_products(region, category, &state.http)
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn get_settings(state: tauri::State<'_, AppState>) -> Settings {
    state.settings_snapshot()
}

#[tauri::command]
fn get_community_status(state: tauri::State<'_, AppState>) -> community::Status {
    state.community.snapshot()
}

#[tauri::command]
async fn save_settings(
    state: tauri::State<'_, AppState>,
    settings: Settings,
) -> Result<Settings, String> {
    let mut next = settings;
    monitor_policy::apply(&mut next);
    next.normalize();
    state.catalog.attach_companions(&mut next.targets);
    state.catalog.attach_locations(&mut next.targets);

    if next.targets != state.settings_snapshot().targets && state.checkout.engaged() {
        return Err("请先停止自动选购，再修改监控目标；当前结账目标不会中途改变".into());
    }

    // 设置里的目标列表和查询间隔要同步给引擎，否则改完设置监控还按旧的跑。
    state.watcher.set_targets(next.targets.clone()).await;
    state.watcher.set_interval(next.interval()).await;
    state.community.set_targets(&next.targets);

    state.put_settings(next.clone())?;
    Ok(next)
}

/// 用界面上的推送地址列表整体替换现有的。
///
/// 每个地址是 Bark 还是飞书由 [`Settings::set_push_urls`] 按长相判断，界面不自己猜。
/// 只动推送地址：不经过 [`save_settings`]，免得顺带把引擎的目标、间隔和代理
/// 也重新下发一遍。
#[tauri::command]
async fn set_push_urls(
    state: tauri::State<'_, AppState>,
    urls: Vec<String>,
) -> Result<Settings, String> {
    let mut next = state.settings_snapshot();
    next.set_push_urls(&urls);
    next.normalize();
    state.put_settings(next.clone())?;
    Ok(next)
}

#[tauri::command]
async fn get_snapshot(state: tauri::State<'_, AppState>) -> Result<Vec<TargetState>, String> {
    Ok(state.watcher.snapshot().await)
}

#[tauri::command]
async fn set_targets(
    state: tauri::State<'_, AppState>,
    targets: Vec<Target>,
) -> Result<Vec<TargetState>, String> {
    if state.checkout.engaged() {
        return Err("请先停止自动选购，再修改监控目标".into());
    }
    let mut targets = targets;
    state.catalog.attach_companions(&mut targets);
    state.catalog.attach_locations(&mut targets);
    state.watcher.set_targets(targets.clone()).await;
    state.community.set_targets(&targets);
    let mut next = state.settings_snapshot();
    next.targets = targets;
    state.put_settings(next)?;
    Ok(state.watcher.snapshot().await)
}

#[tauri::command]
async fn set_interval(state: tauri::State<'_, AppState>, seconds: u64) -> Result<u64, String> {
    let _ = seconds;
    let secs = monitor_policy::INTERVAL_SECONDS;
    state.watcher.set_interval(Duration::from_secs(secs)).await;
    let mut next = state.settings_snapshot();
    next.interval_seconds = secs;
    state.put_settings(next)?;
    Ok(secs)
}

#[tauri::command]
async fn start_watching(app: AppHandle, state: tauri::State<'_, AppState>) -> Result<(), String> {
    let status = tauri::async_runtime::spawn_blocking(move || {
        app.state::<AppState>()
            .checkout
            .request(serde_json::json!({"op":"status"}))
    })
    .await
    .map_err(|_| "无法核对选购状态")??;
    if checkout::access_restricted(&status) {
        return Err("选购请求被 Apple 拒绝；请在限制解除后完成测试演练，再恢复监控。".into());
    }
    state.watcher.start().await;
    state.community.set_running(true);
    Ok(())
}

#[tauri::command]
async fn stop_watching(state: tauri::State<'_, AppState>) -> Result<(), String> {
    state.community.set_running(false);
    state.checkout.stop_if_started();
    state.watcher.stop().await;
    Ok(())
}

#[tauri::command]
async fn is_running(state: tauri::State<'_, AppState>) -> Result<bool, String> {
    Ok(state.watcher.is_running().await)
}

/// 一个待安装的更新。
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct UpdateInfo {
    version: String,
    current_version: String,
    notes: Option<String>,
}

/// 查询有没有新版本。返回 `None` 表示已经是最新。
///
/// **刻意不做静默自动安装**：给用户装东西这件事应该由用户点头。何况这是个会在
/// 抢购当口挂着的程序，自作主张地下载、替换、重启，正好会赶上最不该被打断的时刻。
#[tauri::command]
async fn check_for_update(app: AppHandle) -> Result<Option<UpdateInfo>, String> {
    // Independent fork must never install upstream binaries.
    if app
        .config()
        .identifier
        .starts_with("local.apple-pickup-watcher.auto")
    {
        return Ok(None);
    }
    let updater = app.updater().map_err(|e| e.to_string())?;
    match updater.check().await {
        Ok(Some(update)) => Ok(Some(UpdateInfo {
            version: update.version.clone(),
            current_version: update.current_version.clone(),
            notes: update.body.clone(),
        })),
        Ok(None) => Ok(None),
        // 检查更新失败不是错误状态，只是这次没查到 —— 网络不通、GitHub 抽风都
        // 会走到这里，没必要弹给用户看，写进日志即可。
        Err(err) => Err(err.to_string()),
    }
}

/// 下载并安装更新。安装完成后需要重启应用才生效。
#[tauri::command]
async fn install_update(app: AppHandle) -> Result<(), String> {
    if app
        .config()
        .identifier
        .starts_with("local.apple-pickup-watcher.auto")
    {
        return Err("独立增强版不接收原版自动更新".into());
    }
    let updater = app.updater().map_err(|e| e.to_string())?;
    let update = updater
        .check()
        .await
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "已经是最新版本".to_string())?;

    let handle = app.clone();
    update
        .download_and_install(
            move |downloaded, total| {
                // 进度只发给界面，不做任何决策。
                let _ = handle.emit(
                    "watcher://update-progress",
                    serde_json::json!({ "downloaded": downloaded, "total": total }),
                );
            },
            || {},
        )
        .await
        .map_err(|e| e.to_string())?;

    Ok(())
}

/// 手动试一次提醒，让用户在真正抢购之前确认铃声和推送都通。
#[tauri::command]
async fn test_notify(app: AppHandle) -> Result<(), String> {
    dispatch_notification(
        &app,
        // 标题刻意和真实到货提醒一样带「有货了」：飞书自定义机器人的关键词
        // 安全模式只放行含关键词的消息，测试文案若不含，用户会误以为配置坏了。
        Notification::new("有货了（测试）", "如果你看到并听到了这条，说明提醒是通的"),
    )
    .await
    .map_err(|e| e.to_string())
}

#[tauri::command]
async fn checkout_command(
    app: AppHandle,
    op: String,
    payload: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    if ![
        "status",
        "configure",
        "import",
        "arm",
        "disarm",
        "stop",
        "continue",
        "rehearse",
        "purchase",
        "save_profile",
        "read_profile",
        "acknowledge_order",
        "login",
        "manual",
        "end_run",
    ]
    .contains(&op.as_str())
    {
        return Err("不支持的选购操作".into());
    }
    let mut message = payload.unwrap_or_else(|| serde_json::json!({}));
    if !message.is_object() {
        return Err("参数格式错误".into());
    }
    message["op"] = serde_json::json!(op);
    if op == "rehearse" || op == "manual" {
        let state = app.state::<AppState>();
        let key = message["targetKey"]
            .as_str()
            .ok_or("请选择监控列表中的一个目标")?;
        let settings = state.settings_snapshot();
        let target = settings
            .targets
            .iter()
            .find(|t| t.key().0 == key)
            .ok_or("目标已变更，请重新选择")?;
        let product = state
            .catalog
            .product_by_part(&target.locale, &target.part_number)
            .ok_or("商品目录未匹配")?;
        // A user-requested rehearsal is not an inventory observation.
        // Do not fabricate in-stock state or a fresh observation timestamp.
        let mut hit = serde_json::json!({"target": target});
        hit["product"] = serde_json::to_value(product).map_err(|_| "商品数据不可读")?;
        message["hit"] = hit;
    }
    if op == "purchase" {
        let state = app.state::<AppState>();
        let key = message["targetKey"].as_str().ok_or("请选择有货目标")?;
        let settings = state.settings_snapshot();
        let snapshot = state.watcher.snapshot().await;
        // Preserve the real observation. An explicit manual selection is
        // checked against the official product page, without inventing a hit.
        message["hit"] =
            checkout_hits::for_manual(key, &settings.targets, &snapshot, &state.catalog)?;
    }
    tauri::async_runtime::spawn_blocking(move || app.state::<AppState>().checkout.request(message))
        .await
        .map_err(|_| "选购通信线程异常".to_string())?
}

/// 发出一条提醒：系统通知及用户配置的推送，本地提示音已停用。
async fn dispatch_notification(
    app: &AppHandle,
    notification: Notification,
) -> Result<(), apw_core::notify::NotifyError> {
    let settings = match app.try_state::<AppState>() {
        Some(state) => state.settings_snapshot(),
        None => return Ok(()),
    };

    // 系统通知总是发。它是最轻量也最可靠的一条，没有理由让用户关掉它之后
    // 就完全收不到东西。
    emit_system_notification(app, &notification);

    let mut channels = Multi::new();
    // 停用本地提示音，旧设置中启用了声音也不会播放。
    // if settings.sound_enabled {
    //     channels.push(apw_core::notify::Sound::embedded());
    // }
    if !settings.bark_url.trim().is_empty() {
        let http = app
            .try_state::<AppState>()
            .map(|s| s.http.clone())
            .unwrap_or_default();
        // Bark 每次现构造：地址是用户随时可改的设置项，缓存实例会在改完地址后
        // 继续往旧地址推。共享的 http 客户端一并传进去，连接池仍然复用。
        // 用户可以填多个地址（分号分隔），每个地址一条渠道，并发推送、各自报错。
        for bark in Bark::from_list(&settings.bark_url, http) {
            channels.push(bark);
        }
    }
    if !settings.feishu_webhook.trim().is_empty() {
        let http = app
            .try_state::<AppState>()
            .map(|s| s.http.clone())
            .unwrap_or_default();
        // 飞书与 Bark 同样的构造规则：地址随时可改、支持分号分隔多个、
        // 共享连接池，渠道各自报错。
        for feishu in Feishu::from_list(&settings.feishu_webhook, http) {
            channels.push(feishu);
        }
    }
    if channels.is_empty() {
        return Ok(());
    }
    channels.notify(&notification).await
}

fn emit_system_notification(app: &AppHandle, n: &Notification) {
    use tauri_plugin_notification::NotificationExt;
    // 通知发不出去（用户在系统里关了权限）不该影响其他渠道，也不该让流程中断。
    if let Err(err) = app
        .notification()
        .builder()
        .title(&n.title)
        .body(&n.body)
        .show()
    {
        let _ = app.emit(NOTICE_CHANNEL, format!("系统通知发送失败：{err}"));
    }
}

/// 消费引擎事件：转发给前端，并在有货时发提醒。
async fn pump_events(app: AppHandle, mut events: tokio::sync::mpsc::Receiver<Event>) {
    let mut auto_open_bag = AutoOpenBag::default();
    let (notifications, pending) = notification_queue::channel();
    let notification_app = app.clone();
    tauri::async_runtime::spawn(notification_queue::run(pending, move |notification| {
        let app = notification_app.clone();
        async move {
            if let Err(err) = dispatch_notification(&app, notification).await {
                let _ = app.emit(NOTICE_CHANNEL, format!("发送提醒时出错：{err}"));
            }
        }
    }));

    while let Some(event) = events.recv().await {
        // 先原样转发。前端拿到的事件流应当与引擎发出的完全一致，
        // 中间少一层可能出错的翻译。
        let _ = app.emit(EVENT_CHANNEL, &event);

        if let Event::CycleComplete { .. } = &event {
            let state = app.state::<AppState>();
            let settings = state.settings_snapshot();
            let hits = checkout_hits::for_event(&event, &settings.targets, &state.catalog);
            if !hits.is_empty() {
                let handle = app.clone();
                let _ = tauri::async_runtime::spawn_blocking(move || {
                    handle
                        .state::<AppState>()
                        .checkout
                        .request(serde_json::json!({"op":"offer", "hits":hits}))
                })
                .await;
            }
        }

        if let Event::RunStateChanged { running } = &event {
            auto_open_bag.on_run_state_changed(*running);
        }

        if let Event::InStock { state } = &event {
            let target = &state.target;
            let notification = Notification::new(
                "有货了",
                format!("{} {}", target.store_title, target.product_name),
            );
            let notification = match region_by_locale(&target.locale) {
                Some(region) => notification.with_url(region.bag_url()),
                None => notification,
            };

            let settings = app
                .try_state::<AppState>()
                .map(|s| s.settings_snapshot())
                .unwrap_or_default();

            let mut open_requested = false;
            let _ = auto_open_bag.open_if_needed(
                settings.open_bag_on_hit && !checkout_engaged(&app).await,
                &target.locale,
                |_| {
                    open_requested = true;
                    Ok::<_, ()>(())
                },
            );
            if open_requested {
                let handle = app.clone();
                let locale = target.locale.clone();
                let result = tauri::async_runtime::spawn_blocking(move || {
                    handle
                        .state::<AppState>()
                        .checkout
                        .request(serde_json::json!({"op":"open_bag_if_idle","locale":locale}))
                })
                .await;
                if let Ok(Err(err)) = result {
                    let _ = app.emit(NOTICE_CHANNEL, format!("打开购物袋失败：{err}"));
                }
            }

            // 不逐项等待网络推送，否则15个Bark超时会让快照和暂停状态迟到150秒。
            // 队列满时保留背压，不丢提醒，也不无限创建后台任务。
            if notifications.send(notification).await.is_err() {
                let _ = app.emit(NOTICE_CHANNEL, "提醒发送任务已停止，无法发送到货提醒");
            }
        }
    }
}

async fn checkout_engaged(app: &AppHandle) -> bool {
    let h = app.clone();
    match tauri::async_runtime::spawn_blocking(move || {
        h.state::<AppState>()
            .checkout
            .request(serde_json::json!({"op":"status"}))
    })
    .await
    {
        Ok(Ok(s)) => s["armed"] == true || s["busy"] == true || s["stopping"] == true,
        _ => true, // An uncertain checkout must never open an extra cart.
    }
}

/// 建系统托盘。
///
/// 关窗口时不退出而是收进托盘：这个工具的正常用法就是挂上几个小时等发售，
/// 让它一直占着一个窗口和 Dock 图标没有道理。
fn setup_tray(app: &AppHandle) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, "show", "显示窗口", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &quit])?;

    TrayIconBuilder::with_id("main")
        .icon(
            app.default_window_icon().cloned().ok_or_else(|| {
                tauri::Error::AssetNotFound("默认窗口图标缺失，无法建立托盘".into())
            })?,
        )
        .tooltip("拾漏锁单")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show" => reveal_window(app),
            "quit" => {
                app.state::<AppState>().checkout.stop_if_started();
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            use tauri::tray::{MouseButton, MouseButtonState, TrayIconEvent};
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                reveal_window(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

fn reveal_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

/// 载入设置。读不出来时放弃写盘并留档原文件。
///
/// 这一条是刻意的：读不到旧配置**不等于**用户没有配置。若照常写盘，界面初始化
/// 时的几次控件赋值就会把仅存的那份原子替换成一份空的默认配置，监控列表再也
/// 找不回来。Go 版正是这么丢过数据。
fn load_settings(notices: &mut Vec<String>) -> (Settings, Option<SettingsStore>) {
    let directory = if cfg!(feature = "international-preview") {
        "apple-pickup-watcher-international"
    } else {
        "apple-pickup-watcher-auto"
    };
    let store = match dirs::config_dir()
        .ok_or(apw_core::config::ConfigError::NoConfigDir)
        .map(|p| SettingsStore::at(p.join(directory).join("settings.v2.json")))
    {
        Ok(s) => s,
        Err(err) => {
            notices.push(format!("配置目录不可用，本次运行的设置不会被保存：{err}"));
            return (Settings::default(), None);
        }
    };

    // 「新版配置文件还不存在」才是首次运行的判据。
    //
    // 不能用「目标列表为空」代替：用户删光目标后保存的是一份合法的空目标配置，
    // 而旧版 settings.json 是刻意保留不删的（为了能回退），于是下次启动会把他
    // 亲手删掉的目标连同 locale、Bark 地址一起原样倒回来。
    let first_run = store.path().symlink_metadata().is_err();

    if !cfg!(feature = "international-preview")
        && first_run
        && let Ok(original) = SettingsStore::new()
        && original.path().is_file()
    {
        match original.load() {
            Ok(settings) => {
                if let Err(err) = store.save(&settings) {
                    notices.push(format!("原版设置已读取但独立副本未保存：{err}"));
                    return (settings, None);
                }
                notices.push("已复制原版监控设置；原版不变，监控和选购均未启动".into());
                return (settings, Some(store));
            }
            Err(_) => notices.push("原版设置读取失败；未修改原文件，请在本版重新配置".into()),
        }
    }

    match store.load() {
        Ok(settings) => {
            if first_run
                && let Some(legacy) = store.import_legacy()
                && !legacy.targets.is_empty()
            {
                notices.push(format!(
                    "已从旧版设置迁移了 {} 条监控目标。",
                    legacy.targets.len()
                ));
                // 立刻落盘。否则用户不改任何设置时新版文件一直不存在，
                // 每次启动都要重迁一遍，用户删掉的目标也会一直复活。
                if let Err(err) = store.save(&legacy) {
                    notices.push(format!("迁移结果暂时没能保存：{err}"));
                }
                return (legacy, Some(store));
            }
            (settings, Some(store))
        }
        Err(err) => {
            match store.preserve_corrupted() {
                Ok(Some(path)) => {
                    notices.push(format!("读取设置失败，原文件已备份到 {}", path.display()))
                }
                Ok(None) => {}
                Err(backup_err) => {
                    notices.push(format!("读取设置失败，且无法备份原文件：{backup_err}"));
                }
            }
            notices.push(format!(
                "读取设置失败，已回退到默认设置，并且本次运行不会覆盖磁盘上的配置：{err}"
            ));
            (Settings::default(), None)
        }
    }
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            let mut notices = Vec::new();
            let (mut settings, store) = load_settings(&mut notices);
            monitor_policy::apply(&mut settings);

            // 旧版本保存的 Apple Watch 目标没有搭档表带，查出来会是假的
            // 「无货」；启动时按目录补齐，下次写盘就带上了。
            let catalog = Catalog::new();
            catalog.attach_companions(&mut settings.targets);
            catalog.attach_locations(&mut settings.targets);

            // Desktop monitoring uses the original main-branch scheduler and
            // current system network. Saved legacy proxy addresses are not used.
            let client = AppleClient::new(ClientConfig::default())
                .map_err(|e| format!("构造 Apple 客户端失败：{e}"))?;

            // 用 Watcher::new 而不是 Watcher::spawn：setup 回调跑在主线程上，
            // 并不处在 tokio 运行时上下文里，在这里 tokio::spawn 会 panic，
            // 而且因为发生在不可展开的回调中，进程会直接 abort。
            // 引擎任务交给 Tauri 自己的运行时去驱动。
            let (watcher, events, engine) = Watcher::new(
                client.clone(),
                WatcherConfig {
                    interval: Duration::from_secs(monitor_policy::INTERVAL_SECONDS),
                    ..WatcherConfig::default()
                },
            );
            tauri::async_runtime::spawn(engine);

            {
                let watcher = watcher.clone();
                let targets = settings.targets.clone();
                let interval = settings.interval();
                tauri::async_runtime::spawn(async move {
                    watcher.set_targets(targets).await;
                    watcher.set_interval(interval).await;
                });
            }

            let (community, community_control) =
                community::Community::new(settings.targets.clone());
            let community_client = apw_core::community::Client::new()?;
            app.manage(AppState {
                checkout: checkout::Checkout::new(app.path().resource_dir()?.join(
                    if cfg!(target_os = "windows") {
                        "resources/apw-checkout.exe"
                    } else if cfg!(feature = "international-preview") {
                        "resources/apw-checkout-international"
                    } else {
                        "resources/apw-checkout"
                    },
                )),
                watcher,
                community,
                catalog,
                http: reqwest::Client::new(),
                settings: RwLock::new(settings),
                store,
            });

            let handle: AppHandle = app.handle().clone();
            tauri::async_runtime::spawn(community::run(
                handle.clone(),
                community_control,
                community_client,
            ));
            tauri::async_runtime::spawn(pump_events(handle.clone(), events));
            let checkout_handle = handle.clone();
            tauri::async_runtime::spawn(async move {
                let (notifications, pending) = notification_queue::channel();
                let notification_app = checkout_handle.clone();
                tauri::async_runtime::spawn(notification_queue::run(
                    pending,
                    move |notification| {
                        let app = notification_app.clone();
                        async move {
                            if dispatch_notification(&app, notification).await.is_err() {
                                let _ = app.emit(
                                    NOTICE_CHANNEL,
                                    "选购处理提醒发送失败，请检查 Bark 连接并测试；选购现场已保留",
                                );
                            }
                        }
                    },
                ));
                let mut notice_id = serde_json::Value::Null;
                loop {
                    tokio::time::sleep(Duration::from_secs(1)).await;
                    let h = checkout_handle.clone();
                    if let Ok(Ok(status)) = tauri::async_runtime::spawn_blocking(move || {
                        h.state::<AppState>()
                            .checkout
                            .request(serde_json::json!({"op":"status"}))
                    })
                    .await
                    {
                        // Update the UI before network notification delivery.
                        let _ = checkout_handle.emit("checkout://status", &status);
                        if checkout::access_restricted(&status) {
                            let state = checkout_handle.state::<AppState>();
                            state.community.set_running(false);
                            if state.watcher.is_running().await {
                                state.watcher.stop().await;
                                let _ = checkout_handle.emit(
                                    NOTICE_CHANNEL,
                                    "官网拒绝选购请求，库存监控已同步停止；请核对官网现场。",
                                );
                            }
                        }
                        let notice = &status["notice"];
                        if !notice.is_null() && notice["id"] != notice_id {
                            notice_id = notice["id"].clone();
                            if notifications
                                .send(Notification::new(
                                    notice["title"].as_str().unwrap_or("选购需要处理"),
                                    notice["body"].as_str().unwrap_or("请核对 Safari"),
                                ))
                                .await
                                .is_err()
                            {
                                let _ = checkout_handle.emit(
                                    NOTICE_CHANNEL,
                                    "选购处理提醒发送任务已停止，请检查通知连接",
                                );
                            }
                        }
                    }
                }
            });

            if let Err(err) = setup_tray(&handle) {
                // 托盘建不起来只是少一项能力，不该让程序起不来。
                notices.push(format!("系统托盘不可用：{err}"));
            }

            if !notices.is_empty() {
                // 界面还没订阅上，稍等一下再发。这些是降级说明，用户必须看见 ——
                // 只写到 stderr 是没用的，用户是双击图标启动的。
                tauri::async_runtime::spawn(async move {
                    tokio::time::sleep(Duration::from_millis(800)).await;
                    for notice in notices {
                        let _ = handle.emit(NOTICE_CHANNEL, notice);
                    }
                });
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // 收进托盘而不是退出。真要退出走托盘菜单里的「退出」。
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .invoke_handler(tauri::generate_handler![
            list_regions,
            list_categories,
            list_stores,
            list_products,
            refresh_products,
            get_settings,
            get_community_status,
            save_settings,
            set_push_urls,
            get_snapshot,
            set_targets,
            set_interval,
            start_watching,
            stop_watching,
            is_running,
            test_notify,
            check_for_update,
            install_update,
            checkout_command,
        ])
        .run(tauri::generate_context!())
        .expect("Tauri 应用启动失败");
}
