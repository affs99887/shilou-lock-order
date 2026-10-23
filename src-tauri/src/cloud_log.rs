//! Provider evidence is retained independently of local classification.
use apw_core::{
    apple::{ApiError, StoreAvailability},
    model::Availability,
};
use serde_json::{Value, json};
pub fn response(store: &str, value: &Value, received: u64) -> Value {
    json!({"origin":"vss","stage":"response","store":store,"atMs":received,
        "receivedAtMs":received,"http":value["http"],
        "providerMessage":value["data"]["message"],"providerStatus":value["data"]["status"],
        "providerSource":value["data"]["source"],"providerWarning":value["data"]["warning"],
        "serverTimestamp":value["data"]["timestamp"],
        "rawResponse":value["responseText"],"truncated":value["responseTruncated"]})
}
// Customer messages are based on the same validated result as inventory, never
// on a success flag alone or the potentially truncated diagnostic response body.
pub fn classified_response(
    store: &str,
    locale: &str,
    parts: &[String],
    value: &Value,
    received: u64,
    parsed: &Result<StoreAvailability, ApiError>,
) -> Value {
    let mut event = response(store, value, received);
    event["locale"] = json!(locale);
    event["parts"] = json!(parts);
    match parsed {
        Ok(result) => {
            let mut in_stock = 0;
            let mut out_of_stock = 0;
            let mut unknown = 0;
            for part in parts {
                match result.parts.get(part).map(|p| &p.availability) {
                    Some(Availability::InStock) => in_stock += 1,
                    Some(Availability::OutOfStock) => out_of_stock += 1,
                    _ => unknown += 1,
                }
            }
            event["outcome"] = json!("checked");
            event["counts"] = json!({"total":parts.len(),"inStock":in_stock,
                "outOfStock":out_of_stock,"unconfirmed":unknown});
        }
        Err(_) => {
            event["outcome"] = json!(if value["http"] == 429 {
                "too_frequent"
            } else if value["data"]["status"] == "rate_limited" {
                "rate_limited"
            } else {
                "unconfirmed"
            });
        }
    }
    event
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn summary_uses_validated_results_and_retains_unknown_specs() {
        let value = json!({"http":200,"data":{"status":"success","store":"R575","region":"zh_CN","timestamp":1000,
            "products":{"A":{"available":false,"pickupDisplay":"unavailable"},"B":{"available":false,"pickupDisplay":"default"}}}});
        let parts = vec!["A".into(), "B".into()];
        let parsed =
            apw_core::cloud::parse_response(&value["data"], "R575", "zh_CN", &parts, 1_020_000);
        let result = classified_response("R575", "zh_CN", &parts, &value, 1_020_000, &parsed);
        assert_eq!(
            result["counts"],
            json!({"total":2,"inStock":0,"outOfStock":1,"unconfirmed":1})
        );
        assert_eq!(result["parts"], json!(["A", "B"]));
        // A successful provider flag cannot override rejected stale data.
        let stale =
            apw_core::cloud::parse_response(&value["data"], "R575", "zh_CN", &parts, 2_000_000);
        let result = classified_response("R575", "zh_CN", &parts, &value, 2_000_000, &stale);
        assert_eq!(result["outcome"], "unconfirmed");
        assert!(result["counts"].is_null());
    }
    #[test]
    fn rate_limit_and_request_frequency_remain_distinct() {
        for (http, status, expected) in [
            (200, "rate_limited", "rate_limited"),
            (429, "error", "too_frequent"),
            (503, "error", "unconfirmed"),
        ] {
            let result = classified_response(
                "R575",
                "zh_CN",
                &["A".into()],
                &json!({"http":http,"data":{"status":status}}),
                1,
                &Err(ApiError::RateLimited("internal".into())),
            );
            assert_eq!(result["outcome"], expected);
            assert!(result["counts"].is_null());
        }
    }
    #[test]
    fn preserves_provider_message_even_when_classification_would_differ() {
        let msg = "请稍候 15 秒后重试";
        let result = response(
            "R575",
            &json!({"http":200,"data":{"message":msg,"status":"rate_limited","source":"system_fallback","warning":"cooldown_active"},"responseText":"original"}),
            1,
        );
        assert_eq!(result["providerMessage"], msg);
        assert_eq!(result["rawResponse"], "original");
        assert_eq!(result["providerWarning"], "cooldown_active");
    }
    #[test]
    fn missing_message_and_http_error_never_gain_an_invented_explanation() {
        let result = response(
            "R575",
            &json!({"http":429,"responseText":"busy","data":null}),
            1,
        );
        assert!(result["providerMessage"].is_null());
        assert!(result["providerStatus"].is_null());
        assert_eq!(result["http"], 429);
        assert_eq!(result["rawResponse"], "busy");
    }
}
