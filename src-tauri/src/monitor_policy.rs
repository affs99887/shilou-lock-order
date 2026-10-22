//! Desktop policy: one-minute polling with local alert sounds disabled.
use apw_core::config::Settings;

pub const INTERVAL_SECONDS: u64 = 60;

pub fn apply(settings: &mut Settings) {
    settings.interval_seconds = INTERVAL_SECONDS;
    settings.sound_enabled = false;
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
}
