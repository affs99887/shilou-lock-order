"""Offline arrival-during-checkout reproduction; no browser or profile access."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "checkout"))
from worker import Bridge, STORES
from test_worker import FakeCheckout, hit


class HeldCheckout(FakeCheckout):
    def __init__(self, owner, plan):
        super().__init__(owner, plan)
        self.entered = threading.Event()
        self.resume = threading.Event()
        self.starts = 0

    def command(self, name):
        if name == "rehearse":
            self.starts += 1
        super().command(name)

    def tick(self):
        self.entered.set()
        if not self.resume.wait(10):
            raise RuntimeError("diagnostic timed out")
        self.owner.closed = True


def fresh(other=False):
    value = hit()
    value["lastCheckedMs"] = time.time() * 1000
    if other:
        value["target"]["storeNumber"] = STORES[1]["id"]
        value["product"]["capacity"] = "512GB"
    return value


def main():
    results = {}
    with tempfile.TemporaryDirectory() as directory:
        bridge = Bridge(HeldCheckout, data=Path(directory))
        bridge.armed = True
        bridge.request({"op": "offer", "hits": [fresh()]})
        with patch.object(Bridge, "require_profile", return_value={}), \
                patch.object(Bridge, "acquire"):
            worker = threading.Thread(target=bridge.run)
            worker.start()
            try:
                assert bridge.service.entered.wait(3), "worker did not start"
                original = copy.deepcopy(bridge.service.preferences)
                original_key = bridge.current_key
                checked = []
                for stage in ("PRODUCT", "BAG", "FULFILLMENT", "CONTACT",
                              "PAYMENT", "REVIEW", "LOGIN"):
                    bridge.service.status["stage"] = stage
                    for _ in range(30):
                        bridge.request({"op": "offer", "hits": [fresh(True), fresh()]})
                    assert bridge.tasks.empty(), "new start queued while busy"
                    assert bridge.current_key == original_key, "target replaced"
                    assert bridge.service.preferences == original, "plan replaced"
                    assert bridge.service.starts == 1, "checkout restarted"
                    checked.append(stage)
                results["active_arrivals"] = {
                    "passed": True, "stages": checked, "offers": 210,
                    "starts": bridge.service.starts, "queued_starts": 0,
                    "target_changed": False,
                }
            finally:
                bridge.closed = True
                bridge.service.resume.set()
                worker.join(5)
                assert not worker.is_alive()

    with tempfile.TemporaryDirectory() as directory:
        bridge = Bridge(HeldCheckout, data=Path(directory))
        bridge.armed = True
        barrier = threading.Barrier(16)
        errors = []
        def offer():
            try:
                barrier.wait(3)
                bridge.request({"op": "offer", "hits": [fresh()]})
            except Exception as error:
                errors.append(type(error).__name__)
        threads = [threading.Thread(target=offer) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        assert not errors and all(not thread.is_alive() for thread in threads)
        assert bridge.tasks.qsize() == 1
        results["simultaneous_arrivals"] = {
            "passed": True, "offers": 16, "queued_starts": 1,
        }
    results.update(browser_actions=0, profile_reads=0, apple_requests=0)
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
