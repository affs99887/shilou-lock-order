# 构建与验证

[返回项目首页](../README.md) · [版本说明](MAC-MINUTE.md)

以下构建与验证记录对应 `0.6.2-minute.7`。

## 源码结构

| 目录 | 职责 |
| --- | --- |
| `src/` | React 界面、方案编辑与前端状态 |
| `src-tauri/` | 桌面窗口、托盘、IPC、通知及选购子进程桥接 |
| `crates/apw-core/` | Apple 客户端、库存判定、调度、商品目录与配置 |
| `crates/apw-cli/` | 独立查询与监听 CLI |
| `checkout/` | Python 选购流程、Safari 适配与重复提交保护 |
| `tests/` | 前端交互和异步状态回归测试 |

## 在 macOS 构建

需要 Xcode Command Line Tools、Rust（最低版本见 `Cargo.toml`）、Node.js LTS、pnpm 9 和 Python 3。当前打包配置面向 macOS 13 及以上；本版本构建验证的平台为 Apple Silicon。

```sh
git clone https://github.com/affs99887/shilou-lock-order.git
cd shilou-lock-order
pnpm install --frozen-lockfile

python3 -m venv .venv
source .venv/bin/activate
python -m pip install pyinstaller pyobjc-framework-Cocoa certifi
python checkout/build.py

python scripts/mac-source.py
pnpm tauri build --bundles app -- --locked
```

`checkout/build.py` 生成包含 Python 运行时的选购辅助程序，再由 Tauri 一起打包。辅助程序二进制、构建缓存、用户设置和钥匙串内容不提交到本仓库。

应用为本地临时签名构建，未经 Apple 公证。源码中的 Windows 与其他地区适配不代表本版本已经完成这些平台的发布验证。可选浏览器检查脚本需要本地安装 `playwright` 和 Google Chrome；不属于常规测试。

## 验证

本版本通过 197 项离线测试：前端 78 项、桌面库 11 项、核心库与库存客户端／监控 108 项；TypeScript / Vite 构建及 Mac 应用构建成功。深浅主题及查询异常展示使用模拟数据验证。

```sh
pnpm test
pnpm build
cargo test --locked -p apw-app --lib
cargo test --locked -p apw-core --lib --test watcher --test apple_client
```

常规测试使用本地模拟响应，不添加 `live` 特性。没有验证真实订单提交或付款；选购逻辑的验证边界见 [FORK.md](../FORK.md)。

