# 拾漏锁单 — independent Apple Pickup Watcher fork

auto.4 rebrands the display name and icon and reorganizes the UI into a monitoring
center, target selection, checkout plan and notification settings. Bundle ID and
data locations are unchanged. The checkout helper is byte-identical to auto.3;
monitoring and checkout behavior are not changed by this presentation release.

Based on ENCHIGO/apple-pickup-watcher v0.6.2, commit
fc4b61e82f4789a7e25bf15a4291a3a480069359. Original LICENSE and NOTICE are
retained. This is not an upstream release or an Apple-endorsed product.

The monitoring engine, batching, network policy, availability truth and
notification mechanisms remain upstream. New code attaches a private checkout
helper to completed inventory snapshots, not to foreground JavaScript events.

`checkout/vendor` contains a snapshot of the user's existing checkout adapter
from consumer/monitor-redesign-release (0.9.0-preview); its monitoring worker is
never started. `pickup_monitor` is imported only for checkout-date parsing.
Source is included with the fork; preserve all upstream licensing and notices
when redistributing. No customer settings or credentials belong in a package.

Independent identity: local.apple-pickup-watcher.auto. Monitoring settings:
apple-pickup-watcher-auto/settings.v2.json. Checkout settings:
~/Library/Application Support/ApplePickupWatcherAuto/checkout.json.
Original app packages, sources and settings remain unchanged. Settings import
is explicit; Keychain contact data is read only when the user arms/rehearses.
No Safari cookies, passwords or verification codes are imported.

Current validation boundary: one Safari checkout; max two items; store and
dates checked on the official page; unknown cart writes stop for inspection.
Version auto.3 adds observed provider/term presets and an opt-in formal
order-submit mode. It defaults OFF and requires a separate confirmation when
arming. Rehearsals never submit, regardless of the saved setting. The helper
rechecks review items/budget/fulfillment/fees and keeps a durable one-shot ledger;
unknown results block additional attempts until the user verifies Apple orders.
No payment, financing application, credit authorization or signing is automated.
Pending-payment recognition is conservative and has only synthetic acceptance
coverage; live unpaid-order creation and mobile payment handoff remain UNVERIFIED.
The current observation fixture documents provider choices and review-page
inspection, not an end-to-end packaged engine acceptance.
Manual start (mainland China only) runs the same single attempt without a
monitor hit. At pickup it may switch to another same-city store whose option is
enabled and not marked unavailable; store choice is a reversible selection and
never re-adds to the bag. This switching has offline coverage only.
The live cart-removal 404 observed in the prior candidate remains an outstanding
acceptance item; offline tests do not constitute a verified live checkout.

Build on Mac: install locked frontend dependencies, run checkout/build.py with
the build Python (PyInstaller/PyObjC), then `npm run tauri -- build --bundles app`.
The resulting app bundles Python. A locally ad-hoc signed build is not notarized.
