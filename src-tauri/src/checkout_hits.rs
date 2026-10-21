//! Forward individually confirmed stock; an unrelated failed row is not a veto.
use apw_core::{catalog::Catalog, model::Target, watcher::Event};
use serde_json::Value;

pub fn for_manual(
    key: &str,
    targets: &[Target],
    snapshot: &[apw_core::watcher::TargetState],
    catalog: &Catalog,
) -> Result<Value, String> {
    let target = targets
        .iter()
        .find(|t| t.key().0 == key)
        .ok_or("目标已变更，请重新选择")?;
    let row = snapshot
        .iter()
        .find(|row| row.target.key() == target.key() && row.availability.is_in_stock())
        .ok_or("该目标当前未确认有货，请等待库存更新")?;
    let product = catalog
        .product_by_part(&target.locale, &target.part_number)
        .ok_or("商品目录未匹配")?;
    let mut hit = serde_json::to_value(row).map_err(|_| "库存数据不可读")?;
    hit["product"] = serde_json::to_value(product).map_err(|_| "商品数据不可读")?;
    Ok(hit)
}

pub fn for_event(event: &Event, targets: &[Target], catalog: &Catalog) -> Vec<Value> {
    let Event::CycleComplete { snapshot, .. } = event else {
        return Vec::new();
    };
    targets
        .iter()
        .filter_map(|target| {
            let row = snapshot
                .iter()
                .find(|row| row.target.key() == target.key() && row.availability.is_in_stock())?;
            let product = catalog.product_by_part(&target.locale, &target.part_number)?;
            let mut hit = serde_json::to_value(row).ok()?;
            hit["product"] = serde_json::to_value(product).ok()?;
            // Keep the actual timestamp. The worker checks freshness both before
            // queueing and after profile/ownership checks, before browser actions.
            Some(hit)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use apw_core::{
        model::{Availability, UnknownReason},
        watcher::TargetState,
    };

    fn row() -> TargetState {
        let fixture: Value =
            serde_json::from_str(include_str!("../../checkout/fixtures/apw-hit.json")).unwrap();
        TargetState {
            target: serde_json::from_value(fixture["target"].clone()).unwrap(),
            availability: Availability::InStock,
            last_checked_ms: Some(100000),
            consecutive_failures: 0,
        }
    }

    #[test]
    fn manual_selection_preserves_real_observation_and_never_accepts_removed_or_unknown_targets() {
        let good = row();
        let targets = vec![good.target.clone()];
        let catalog = Catalog::new();
        let hit = for_manual(
            &good.target.key().0,
            &targets,
            std::slice::from_ref(&good),
            &catalog,
        )
        .unwrap();
        assert_eq!(hit["target"]["storeNumber"], "R683");
        assert_eq!(hit["lastCheckedMs"], 100000);
        assert!(
            for_manual(
                &good.target.key().0,
                &[],
                std::slice::from_ref(&good),
                &catalog
            )
            .is_err()
        );
        for availability in [
            Availability::OutOfStock,
            Availability::Unknown(UnknownReason::NotYetChecked),
        ] {
            let mut bad = row();
            bad.availability = availability;
            assert!(for_manual(&good.target.key().0, &targets, &[bad], &catalog).is_err());
        }
    }

    #[test]
    fn another_store_failure_does_not_discard_a_confirmed_hit() {
        let good = row();
        let mut bad = row();
        bad.target.store_number = "R575".into();
        bad.availability = Availability::Unknown(UnknownReason::NotYetChecked);
        let targets = vec![bad.target.clone(), good.target.clone()];
        let event = Event::CycleComplete {
            next_check_in_secs: 240,
            paced: true,
            healthy: false,
            snapshot: vec![bad, good],
        };
        let hits = for_event(&event, &targets, &Catalog::new());
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0]["target"]["storeNumber"], "R683");
        assert_eq!(hits[0]["lastCheckedMs"], 100000);
    }

    #[test]
    fn unconfigured_or_unknown_rows_never_trigger_and_priority_is_preserved() {
        let first = row();
        let mut second = row();
        second.target.store_number = "R575".into();
        let event = Event::CycleComplete {
            next_check_in_secs: 30,
            paced: false,
            healthy: true,
            snapshot: vec![first.clone(), second.clone()],
        };
        let catalog = Catalog::new();
        let hits = for_event(
            &event,
            &[second.target.clone(), first.target.clone()],
            &catalog,
        );
        assert_eq!(hits[0]["target"]["storeNumber"], "R575");
        assert_eq!(hits[1]["target"]["storeNumber"], "R683");
        assert!(for_event(&event, &[], &catalog).is_empty());
        assert!(
            for_event(
                &Event::StateChanged {
                    state: first.clone()
                },
                &[first.target],
                &catalog
            )
            .is_empty()
        );
    }
}
