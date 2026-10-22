//! 监控连续一段时间拿不到任何库存结果时推送一次提醒，恢复后再推一次。
//!
//! 代理全连不上、节点全被拦时，列表里只会是一排「未知」，人不在电脑前就不知道
//! 监控已经查不到东西了。

use std::time::{Duration, Instant};

/// 连续多久拿不到明确结果才提醒。太短会被偶发失败打扰，太长会错过开售。
pub(crate) const FAILING_AFTER: Duration = Duration::from_secs(3 * 60);

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Alert {
    /// 连续 [`FAILING_AFTER`] 没有拿到任何有货或无货的结果。
    Failing,
    /// 提醒过之后，又拿到了结果。
    Recovered,
}

#[derive(Default)]
pub(crate) struct FailureAlert {
    failing_since: Option<Instant>,
    alerted: bool,
}

impl FailureAlert {
    /// 开始和暂停都从头计时：暂停期间本来就不查询，不算「查不到」。
    pub(crate) fn on_run_state_changed(&mut self) {
        *self = Self::default();
    }

    /// 一轮查询结束。`answered`：这一轮至少有一个目标拿到了明确的有货或无货。
    pub(crate) fn on_cycle(&mut self, answered: bool, now: Instant) -> Option<Alert> {
        if answered {
            self.failing_since = None;
            return std::mem::take(&mut self.alerted).then_some(Alert::Recovered);
        }
        let since = *self.failing_since.get_or_insert(now);
        if !self.alerted && now.duration_since(since) >= FAILING_AFTER {
            self.alerted = true;
            return Some(Alert::Failing);
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 偶发失败不提醒() {
        let mut alert = FailureAlert::default();
        let t0 = Instant::now();
        assert_eq!(alert.on_cycle(false, t0), None);
        assert_eq!(alert.on_cycle(false, t0 + Duration::from_secs(60)), None);
        assert_eq!(
            alert.on_cycle(true, t0 + Duration::from_secs(65)),
            None,
            "没提醒过就不发恢复"
        );
        assert_eq!(
            alert.on_cycle(false, t0 + Duration::from_secs(200)),
            None,
            "重新计时"
        );
    }

    #[test]
    fn 连续三分钟查不到才提醒且只提醒一次() {
        let mut alert = FailureAlert::default();
        let t0 = Instant::now();
        assert_eq!(alert.on_cycle(false, t0), None);
        assert_eq!(
            alert.on_cycle(false, t0 + FAILING_AFTER - Duration::from_secs(1)),
            None
        );
        assert_eq!(
            alert.on_cycle(false, t0 + FAILING_AFTER),
            Some(Alert::Failing)
        );
        assert_eq!(
            alert.on_cycle(false, t0 + FAILING_AFTER * 3),
            None,
            "不重复提醒"
        );
    }

    #[test]
    fn 恢复后提醒一次并重新计时() {
        let mut alert = FailureAlert::default();
        let t0 = Instant::now();
        alert.on_cycle(false, t0);
        assert_eq!(
            alert.on_cycle(false, t0 + FAILING_AFTER),
            Some(Alert::Failing)
        );
        let t1 = t0 + FAILING_AFTER + Duration::from_secs(5);
        assert_eq!(alert.on_cycle(true, t1), Some(Alert::Recovered));
        assert_eq!(alert.on_cycle(true, t1 + Duration::from_secs(5)), None);
        assert_eq!(alert.on_cycle(false, t1 + Duration::from_secs(10)), None);
        assert_eq!(
            alert.on_cycle(false, t1 + Duration::from_secs(10) + FAILING_AFTER),
            Some(Alert::Failing),
            "再次查不到满三分钟会重新提醒"
        );
    }

    #[test]
    fn 暂停后重新计时() {
        let mut alert = FailureAlert::default();
        let t0 = Instant::now();
        alert.on_cycle(false, t0);
        alert.on_run_state_changed();
        assert_eq!(alert.on_cycle(false, t0 + FAILING_AFTER), None);
    }
}
