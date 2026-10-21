// Offline probe of the production gate with a fixed region resolver.
// The callback records a URL; it never opens a browser or makes a request.
mod apw_core {
    pub mod model {
        pub struct Region;
        impl Region {
            pub fn bag_url(&self) -> String {
                "https://www.apple.com.cn/shop/bag".into()
            }
        }
        pub fn region_by_locale(locale: &str) -> Option<Region> {
            if locale == "zh_CN" { Some(Region) } else { None }
        }
    }
}

#[path = "../src-tauri/src/auto_open.rs"]
mod auto_open;

fn main() {
    let mut buying_app = auto_open::AutoOpenBag::default();
    let mut monitoring_app = auto_open::AutoOpenBag::default();
    buying_app.on_run_state_changed(true);
    monitoring_app.on_run_state_changed(true);
    let mut openings = 0;
    // pump_events uses only this app's helper status for checkout_engaged.
    // App A is buying; App B has no active/armed checkout of its own.
    for (gate, own_checkout_busy) in
        [(&mut buying_app, true), (&mut monitoring_app, false)]
    {
        gate.open_if_needed(true && !own_checkout_busy, "zh_CN", |_| {
            openings += 1;
            Ok::<_, ()>(())
        }).unwrap();
    }
    assert_eq!(openings, 1);
    println!("{{\"independent_instance_gate_allows_open\":true,\"recorded_open_callbacks\":1,\"actual_browser_actions\":0,\"apple_requests\":0}}");
}
