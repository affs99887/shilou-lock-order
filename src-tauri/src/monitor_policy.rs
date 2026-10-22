//! Desktop policy: one-minute polling with local alert sounds disabled.
//! Rotating tunnel proxies may poll as often as every five seconds.
use apw_core::config::{Settings, normalize_proxies};

pub const INTERVAL_SECONDS: u64 = 60;

/// 隧道代理模式下允许的最短间隔。每次查询都换出口 IP 和会话，按 IP 累计的次数
/// 攒不起来才敢这么快；本机直连仍固定一分钟一次。
pub const FAST_INTERVAL_SECONDS: u64 = 5;

pub fn apply(settings: &mut Settings) {
    settings.interval_seconds = if rotating(settings) {
        settings
            .interval_seconds
            .clamp(FAST_INTERVAL_SECONDS, INTERVAL_SECONDS)
    } else {
        INTERVAL_SECONDS
    };
    settings.sound_enabled = false;
}

/// 查询是否全部经隧道代理发出：打开了换 IP，且至少有一个合法的代理地址。
pub fn rotating(settings: &Settings) -> bool {
    settings.rotate_proxies && !normalize_proxies(&settings.proxies).is_empty()
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
    fn rotating_proxies_allow_five_to_sixty_seconds() {
        for (previous, applied) in [(0, 5), (5, 5), (10, 10), (30, 30), (60, 60), (3600, 60)] {
            let mut settings = Settings {
                interval_seconds: previous,
                proxies: vec!["http://user:pw@tunnel.example:15818".into()],
                rotate_proxies: true,
                ..Settings::default()
            };
            apply(&mut settings);
            assert_eq!(settings.interval_seconds, applied, "从 {previous} 秒");
            assert!(!settings.sound_enabled);
        }
    }

    #[test]
    fn rotation_without_a_usable_proxy_keeps_one_minute() {
        // 换 IP 开着但没有能用的代理：查询会走本机直连，必须回到一分钟。
        for proxies in [vec![], vec!["not a proxy".to_string()]] {
            let mut settings = Settings {
                interval_seconds: 5,
                proxies,
                rotate_proxies: true,
                ..Settings::default()
            };
            apply(&mut settings);
            assert_eq!(settings.interval_seconds, INTERVAL_SECONDS);
        }
    }
}
