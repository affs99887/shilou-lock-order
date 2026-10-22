//! Public community reports are purchase hints, never confirmed Apple inventory.
//! Only the documented public aggregate endpoint is read; nothing is uploaded.
use std::collections::BTreeMap;
use std::time::Duration;

use chrono::DateTime;
use rand::Rng;
use serde::{Deserialize, Serialize};

use crate::catalog::Catalog;
use crate::model::{Category, Product, Target};

pub const ENDPOINT: &str = "https://sunbelife.com/iphone-stock/api/stats";
pub const SOURCE: &str = "sunbelife_aggregate";
pub const MAX_AGE_MS: u64 = 180_000;
const MAX_BODY: usize = 2 * 1024 * 1024;

pub fn poll_delay(failures: u32) -> u64 {
    rand::rng().random_range(48..=72) * (1_u64 << failures.min(3))
}

#[derive(Debug, Deserialize)]
pub struct Report {
    updated_at: String,
    cities: Vec<City>,
}

#[derive(Debug, Deserialize)]
struct City {
    area: String,
    city: String,
    products: Vec<Variant>,
}

#[derive(Debug, Deserialize)]
struct Variant {
    model: String,
    capacity: String,
    color: String,
    stores: Vec<String>,
    last_at: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Signal {
    pub target: Target,
    pub product: Product,
    pub source: &'static str,
    /// Timestamp of the aggregate product group, not a per-store confirmation.
    pub reported_at_ms: u64,
    pub received_at_ms: u64,
}

fn timestamp(value: &str) -> Option<u64> {
    u64::try_from(DateTime::parse_from_rfc3339(value).ok()?.timestamp_millis()).ok()
}

fn fresh(stamp: u64, now: u64) -> bool {
    stamp <= now && now - stamp <= MAX_AGE_MS
}

fn normalized(value: &str) -> String {
    value
        .chars()
        .filter(|c| !c.is_whitespace())
        .flat_map(char::to_lowercase)
        .collect()
}

fn matches_product(variant: &Variant, product: &Product) -> bool {
    product.category == Category::Iphone
        && normalized(&product.capacity) == normalized(&variant.capacity)
        && normalized(product.color.trim_end_matches('色'))
            == normalized(variant.color.trim_end_matches('色'))
        && normalized(product.title.trim_end_matches('色'))
            == normalized(&format!(
                "{} {} {}",
                variant.model,
                variant.capacity,
                variant.color.trim_end_matches('色')
            ))
}

/// Deduplication survives pause/resume within this process. The checkout worker's
/// persisted order guard remains the authority across application restarts.
#[derive(Default)]
pub struct Matcher {
    seen: BTreeMap<String, u64>,
}

impl Matcher {
    pub fn collect(
        &mut self,
        report: &Report,
        targets: &[Target],
        catalog: &Catalog,
        now: u64,
    ) -> Result<Vec<Signal>, String> {
        let updated = timestamp(&report.updated_at)
            .filter(|s| fresh(*s, now))
            .ok_or("共享接口时间无效或响应已过期")?;
        let stores = catalog.stores("zh_CN").map_err(|_| "本地门店目录不可用")?;
        let products = catalog
            .products("zh_CN")
            .map_err(|_| "本地商品目录不可用")?;
        let mut signals = Vec::new();
        // Preserve configured priority; remote strings never choose a SKU or URL.
        for target in targets.iter().filter(|t| t.locale == "zh_CN") {
            let Some(product) = products
                .iter()
                .find(|p| p.part_number == target.part_number)
            else {
                continue;
            };
            let Some(store) = stores.iter().find(|s| s.number == target.store_number) else {
                continue;
            };
            let latest = report
                .cities
                .iter()
                .filter(|c| {
                    c.area == "cn"
                        && normalized(c.city.trim_end_matches('市'))
                            == normalized(store.city.trim_end_matches('市'))
                })
                .flat_map(|c| &c.products)
                .filter(|v| {
                    matches_product(v, product)
                        && products.iter().filter(|p| matches_product(v, p)).count() == 1
                        && v.stores
                            .iter()
                            .any(|s| normalized(s) == normalized(&store.title))
                })
                .filter_map(|v| timestamp(&v.last_at))
                .filter(|s| *s <= updated && fresh(*s, now))
                .max();
            let Some(stamp) = latest else { continue };
            let key = target.key().0;
            if self.seen.get(&key).is_some_and(|old| stamp <= *old) {
                continue;
            }
            self.seen.insert(key, stamp);
            signals.push(Signal {
                target: target.clone(),
                product: product.clone(),
                source: SOURCE,
                reported_at_ms: stamp,
                received_at_ms: now,
            });
        }
        // Keep at most the recent suppression window; duplicate old reports are
        // still rejected by freshness after pruning, even for removed targets.
        self.seen
            .retain(|_, stamp| now.saturating_sub(*stamp) <= MAX_AGE_MS * 2);
        Ok(signals)
    }
}

#[derive(Clone)]
pub struct Client(reqwest::Client);

impl Client {
    pub fn new() -> Result<Self, reqwest::Error> {
        reqwest::Client::builder()
            .timeout(Duration::from_secs(12))
            .redirect(reqwest::redirect::Policy::none())
            .user_agent("ApplePickupWatcher/0.6 public-stock-reader")
            .build()
            .map(Self)
    }

    pub async fn fetch(&self) -> Result<Report, String> {
        let mut response = self
            .0
            .get(ENDPOINT)
            .query(&[("area", "cn"), ("recent", "1")])
            .send()
            .await
            .map_err(|_| "共享线索暂时无法连接".to_string())?;
        if !response.status().is_success() {
            return Err(format!(
                "共享接口返回 HTTP {}，稍后重试",
                response.status().as_u16()
            ));
        }
        if response
            .content_length()
            .is_some_and(|n| n > MAX_BODY as u64)
        {
            return Err("共享接口响应过大".into());
        }
        let mut body = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(|_| "共享接口响应未读完")? {
            if body.len() + chunk.len() > MAX_BODY {
                return Err("共享接口响应过大".into());
            }
            body.extend_from_slice(&chunk);
        }
        serde_json::from_slice(&body).map_err(|_| "共享接口结构无法识别；本机查询继续运行".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};

    fn fixture() -> (Target, Value, u64) {
        let hit: Value =
            serde_json::from_str(include_str!("../../../checkout/fixtures/apw-hit.json")).unwrap();
        let target: Target = serde_json::from_value(hit["target"].clone()).unwrap();
        let product = Catalog::new()
            .product_by_part("zh_CN", &target.part_number)
            .unwrap();
        let model = product
            .title
            .strip_suffix(&format!(" {} {}", product.capacity, product.color))
            .unwrap();
        let data = json!({"updated_at":"2026-10-23T02:00:10+00:00","cities":[{"area":"cn","city":"上海","products":[{
            "model": model, "capacity":product.capacity,"color":product.color,"stores":[target.store_title],"last_at":"2026-10-23T02:00:00+00:00"
        }]}]});
        (target, data, timestamp("2026-10-23T02:00:10Z").unwrap())
    }

    #[test]
    fn exact_matches_are_hints_and_repeated_aggregates_do_not_trigger_again() {
        let (target, data, now) = fixture();
        let report = serde_json::from_value(data).unwrap();
        let mut matcher = Matcher::default();
        let hits = matcher
            .collect(&report, std::slice::from_ref(&target), &Catalog::new(), now)
            .unwrap();
        assert_eq!(hits.len(), 1);
        let wire = serde_json::to_value(&hits[0]).unwrap();
        assert!(wire.get("availability").is_none());
        assert_eq!(wire["source"], SOURCE);
        assert!(
            matcher
                .collect(&report, &[target], &Catalog::new(), now + 1)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn a_new_report_can_trigger_again_but_older_reports_stay_suppressed() {
        let (target, mut data, now) = fixture();
        let mut matcher = Matcher::default();
        let catalog = Catalog::new();
        let targets = [target];
        let original = serde_json::from_value(data.clone()).unwrap();
        assert_eq!(
            matcher
                .collect(&original, &targets, &catalog, now)
                .unwrap()
                .len(),
            1
        );
        data["cities"][0]["products"][0]["last_at"] = json!("2026-10-23T02:00:10Z");
        let newer = serde_json::from_value(data).unwrap();
        assert_eq!(
            matcher
                .collect(&newer, &targets, &catalog, now)
                .unwrap()
                .len(),
            1
        );
        assert!(
            matcher
                .collect(&original, &targets, &catalog, now)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn stale_future_wrong_store_model_color_capacity_and_region_do_not_trigger() {
        let (target, data, now) = fixture();
        for (path, value) in [
            ("/cities/0/area", "hk"),
            ("/cities/0/city", "北京"),
            ("/cities/0/products/0/model", "iPhone wrong"),
            ("/cities/0/products/0/capacity", "4TB"),
            ("/cities/0/products/0/color", "错误颜色"),
            ("/cities/0/products/0/stores/0", "上海-别的门店"),
            ("/cities/0/products/0/last_at", "2026-10-23T01:55:00Z"),
            ("/cities/0/products/0/last_at", "2026-10-23T03:00:00Z"),
        ] {
            let mut bad = data.clone();
            *bad.pointer_mut(path).unwrap() = json!(value);
            let hits = Matcher::default()
                .collect(
                    &serde_json::from_value(bad).unwrap(),
                    std::slice::from_ref(&target),
                    &Catalog::new(),
                    now,
                )
                .unwrap();
            assert!(hits.is_empty(), "{path}");
        }
        let report = serde_json::from_value(data).unwrap();
        assert!(
            Matcher::default()
                .collect(&report, &[], &Catalog::new(), now)
                .unwrap()
                .is_empty()
        );
        assert!(
            Matcher::default()
                .collect(&report, &[target], &Catalog::new(), now + MAX_AGE_MS + 1)
                .is_err()
        );
        assert!(serde_json::from_str::<Report>("{}").is_err());
    }
}
