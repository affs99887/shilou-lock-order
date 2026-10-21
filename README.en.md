# Shilou Lock Order

Apple Store pickup inventory monitoring and assisted Safari checkout. Built with Rust, Tauri 2, React / TypeScript and Python.

[中文说明 and build instructions](README.md) · [Architecture](docs/PROJECT-LOGIC.zh-CN.md) · [Fork history](FORK.md)

Desktop version **0.6.2-minute.7** restores the original 60-second base delay with ±20% jitter and a request budget that refills once per minute. Local stock sounds remain disabled and the dark-mode fixes are retained. Query errors now show their causes and cooldown details. Requests, batching and backoff can extend the actual interval.

Inventory comes directly from Apple's regional `/shop/retail/pickup-message` endpoint. Checkout remains opt-in. Login, verification and actual payment require the user. No live order submission or payment was tested for this version.

197 offline tests passed, together with the frontend build, Apple Silicon Mac build and isolated light/dark UI checks for query errors. This repository contains source and documentation, excluding user settings, credentials, helper binaries and build caches. The Mac build is ad hoc signed, not notarized.

## License

[GPL-3.0-or-later](LICENSE) · [Copyright and third-party notices](NOTICE)

The third-party administrative-division dataset retains its separate SPL 1.0 license; see NOTICE for its scope and terms.

Not affiliated with, authorized by or endorsed by Apple Inc.
