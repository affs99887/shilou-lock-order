//! Strict interpretation of VSS responses. Never treat a fallback or missing SKU as stock.
use crate::apple::{ApiError, PartStatus, StoreAvailability};
use crate::model::{Availability, UnknownReason};
use serde_json::Value;
use std::collections::BTreeMap;

fn schema(field: &str) -> ApiError {
    ApiError::SchemaDrift {
        field: format!("VSS.{field}"),
        raw: "云端数据缺失或与请求不符".into(),
    }
}

pub fn parse_response(
    data: &Value,
    store: &str,
    locale: &str,
    parts: &[String],
    now_ms: u64,
) -> Result<StoreAvailability, ApiError> {
    if data["store"].as_str() != Some(store) || data["region"].as_str() != Some(locale) {
        return Err(schema("store/region"));
    }
    if data["status"] == "rate_limited" || data["source"] == "system_fallback" {
        return Err(ApiError::RateLimited(
            "VSS 云端查询正在冷却，库存未确认".into(),
        ));
    }
    if !matches!(data["status"].as_str(), Some("success" | "ok")) {
        return Err(schema("status"));
    }
    let timestamp = data["timestamp"]
        .as_u64()
        .and_then(|t| t.checked_mul(1000))
        .ok_or_else(|| schema("timestamp"))?;
    if timestamp > now_ms.saturating_add(30_000) || now_ms.saturating_sub(timestamp) > 90_000 {
        return Err(ApiError::Transport(
            "VSS 返回结果已过期或时间异常，等待更新".into(),
        ));
    }
    let products = data["products"]
        .as_object()
        .ok_or_else(|| schema("products"))?;
    let mut parsed = BTreeMap::new();
    for part in parts {
        let row = products.get(part).ok_or_else(|| schema("products/SKU"))?;
        if row["localizedPart"].as_str().is_some_and(|v| v != part)
            || row["part"].as_str().is_some_and(|v| v != part)
        {
            return Err(schema("localizedPart"));
        }
        let display = row["pickupDisplay"].as_str().unwrap_or("");
        let available = row["available"].as_bool();
        let availability = match (display, available) {
            ("available", Some(true)) => Availability::InStock,
            ("unavailable" | "ineligible", Some(false)) => Availability::OutOfStock,
            ("default" | "ships-to-store", _) => {
                Availability::Unknown(UnknownReason::PickupPending)
            }
            ("rate_limited", _) => Availability::Unknown(UnknownReason::RateLimited),
            _ => Availability::Unknown(UnknownReason::SchemaDrift {
                field: "VSS.pickupDisplay/available".into(),
                raw: format!("{display}/{available:?}"),
            }),
        };
        parsed.insert(
            part.clone(),
            PartStatus {
                part_number: part.clone(),
                availability,
                product_title: None,
                pickup_display: display.into(),
            },
        );
    }
    Ok(StoreAvailability {
        store_number: store.into(),
        store_name: store.into(),
        parts: parsed,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn good() -> Value {
        json!({"status":"success","store":"R575","region":"zh_CN","timestamp":1000,"products":{"A":{"part":"A","localizedPart":"A","available":true,"pickupDisplay":"available"}}})
    }
    fn parse(v: &Value) -> Result<StoreAvailability, ApiError> {
        parse_response(v, "R575", "zh_CN", &["A".into()], 1_020_000)
    }
    #[test]
    fn exact_confirmed_stock_only() {
        assert!(
            parse(&good()).unwrap().parts["A"]
                .availability
                .is_in_stock()
        );
    }
    #[test]
    fn wrong_scope_missing_data_and_expired_results_rejected() {
        for (key, value) in [
            ("store", json!("R001")),
            ("region", json!("zh_HK")),
            ("timestamp", json!(800)),
            ("timestamp", json!(2000)),
            ("timestamp", Value::Null),
            ("status", json!("error")),
            ("products", json!({})),
        ] {
            let mut v = good();
            v[key] = value;
            assert!(parse(&v).is_err(), "{key}");
        }
        let mut v = good();
        v["products"]["A"]["localizedPart"] = json!("B");
        assert!(parse(&v).is_err());
    }
    #[test]
    fn fallback_and_limit_are_never_actionable() {
        let mut v = good();
        v["source"] = json!("system_fallback");
        assert!(parse(&v).is_err());
        v = good();
        v["status"] = json!("rate_limited");
        assert!(parse(&v).is_err());
    }
    #[test]
    fn contradictions_and_unrecognized_states_stay_unknown() {
        for display in [
            "available",
            "ships-to-store",
            "default",
            "rate_limited",
            "new_value",
            "",
        ] {
            let mut v = good();
            v["products"]["A"]["available"] = json!(false);
            v["products"]["A"]["pickupDisplay"] = json!(display);
            assert!(matches!(
                parse(&v).unwrap().parts["A"].availability,
                Availability::Unknown(_)
            ));
        }
    }
}
