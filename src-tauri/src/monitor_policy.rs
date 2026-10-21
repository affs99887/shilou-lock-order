//! Desktop policy: five-second polling with local alert sounds disabled.
use apw_core::config::Settings;

pub const INTERVAL_SECONDS: u64 = 5;

pub fn apply(settings: &mut Settings) {
    settings.interval_seconds = INTERVAL_SECONDS;
    settings.sound_enabled = false;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_and_existing_settings_use_five_seconds_and_disable_sound() {
        for previous in [0, 5, 30, 60, 120, 3600] {
            let mut settings = Settings {
                interval_seconds: previous,
                ..Settings::default()
            };
            let mut expected = serde_json::to_value(&settings).unwrap();
            expected["intervalSeconds"] = serde_json::json!(5);
            expected["soundEnabled"] = serde_json::json!(false);
            apply(&mut settings);
            assert_eq!(settings.interval(), std::time::Duration::from_secs(5));
            assert_eq!(serde_json::to_value(&settings).unwrap(), expected);
            apply(&mut settings);
            assert_eq!(serde_json::to_value(&settings).unwrap(), expected);
        }
    }
}
