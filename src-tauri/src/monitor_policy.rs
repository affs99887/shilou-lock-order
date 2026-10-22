//! Desktop policy: one-minute polling with local alert sounds disabled.
//! Proxy-only modes may poll as often as every five seconds.
use apw_core::apple::ProxyMode;
use apw_core::config::{Settings, normalize_proxies};

pub const INTERVAL_SECONDS: u64 = 60;

/// 只用代理查询时允许的最短间隔。查询分散到多个出口 IP 上，每个 IP 摊到的次数
/// 很少才敢这么快；代理轮流模式下每条线路还有自己的请求预算兜着。本机直连参与
/// 查询时仍固定一分钟一次。
pub const FAST_INTERVAL_SECONDS: u64 = 5;

pub fn apply(settings: &mut Settings) {
    settings.interval_seconds = if proxy_only(settings) {
        settings
            .interval_seconds
            .clamp(FAST_INTERVAL_SECONDS, INTERVAL_SECONDS)
    } else {
        INTERVAL_SECONDS
    };
    settings.sound_enabled = false;
}

/// 查询是否全部经代理发出：选了只用代理的模式，且至少有一个合法的代理地址。
pub fn proxy_only(settings: &Settings) -> bool {
    settings.proxy_mode != ProxyMode::Mixed && !normalize_proxies(&settings.proxies).is_empty()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_and_existing_settings_use_one_minute_and_disable_sound() {
        for previous in [0, 5, 30, 60, 120, 3600] {
            let mut settings = Settings {
                interval_seconds: previous,
                bark_url: "https://example.invalid/test-bark".into(),
                proxies: vec!["http://127.0.0.1:7890".into()],
                ..Settings::default()
            };
            let mut expected = serde_json::to_value(&settings).unwrap();
            expected["intervalSeconds"] = serde_json::json!(60);
            expected["soundEnabled"] = serde_json::json!(false);
            apply(&mut settings);
            assert_eq!(settings.interval(), std::time::Duration::from_secs(60));
            assert_eq!(serde_json::to_value(&settings).unwrap(), expected);
            apply(&mut settings);
            assert_eq!(serde_json::to_value(&settings).unwrap(), expected);
        }
    }

    #[test]
    fn proxy_only_modes_allow_five_to_sixty_seconds() {
        for mode in [ProxyMode::Pool, ProxyMode::Tunnel] {
            for (previous, applied) in [(0, 5), (5, 5), (10, 10), (30, 30), (60, 60), (3600, 60)] {
                let mut settings = Settings {
                    interval_seconds: previous,
                    proxies: vec!["http://user:pw@tunnel.example:15818".into()],
                    proxy_mode: mode,
                    ..Settings::default()
                };
                apply(&mut settings);
                assert_eq!(
                    settings.interval_seconds, applied,
                    "{mode:?} 从 {previous} 秒"
                );
                assert!(!settings.sound_enabled);
            }
        }
    }

    #[test]
    fn proxy_only_without_a_usable_proxy_keeps_one_minute() {
        // 选了只用代理但没有能用的代理：查询会走本机直连，必须回到一分钟。
        for proxies in [vec![], vec!["not a proxy".to_string()]] {
            let mut settings = Settings {
                interval_seconds: 5,
                proxies,
                proxy_mode: ProxyMode::Pool,
                ..Settings::default()
            };
            apply(&mut settings);
            assert_eq!(settings.interval_seconds, INTERVAL_SECONDS);
        }
    }
}
