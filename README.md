# 拾漏锁单

Apple 门店到店取货库存监控与选购辅助，基于 Rust、Tauri 2、React / TypeScript 与 Python。

当前桌面版本：**0.6.2-quiet5s.1**。

[English](README.en.md) · [项目逻辑说明](docs/PROJECT-LOGIC.zh-CN.md) · [版本说明](docs/MAC-QUIET5S.md) · [派生说明](FORK.md)

## 功能

- **本地静音**：关闭有货提示音；系统通知、Bark 与飞书保留原有逻辑。
- **5 秒基础间隔，保留随机浮动**：每轮查询结束后，正常情况下等待约 **4–6 秒（±20%）**。桌面请求预算每 5 秒恢复一个额度；请求耗时、多个查询分组、失败退避或冷却会延长实际间隔。
- **深浅主题**：分期卡片、选中期数、原生下拉框、侧栏和提示文字跟随系统主题。
- **库存与选购分工**：Rust 后台查询库存；启用选购后，由 Python 选择合格目标并操作 Safari。自动提交需明确启用和确认；登录、验证码、实际付款等由本人处理。无法确认的提交结果会阻止重复尝试。

库存查询直接调用 Apple 官网各地区的 `/shop/retail/pickup-message`。中国大陆对应 `https://www.apple.com.cn/shop/retail/pickup-message`，不是第三方库存服务。查询失败保留为未知状态；接口行为和库存可随官网变化。

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

本版本通过 126 项相关测试：前端 75 项、桌面库 11 项、库存监控与 Apple 客户端 40 项；TypeScript / Vite 构建及 Mac 应用构建成功，并完成深浅主题下的分期选择交互检查。

```sh
pnpm test
pnpm build
cargo test --locked -p apw-app --lib
cargo test --locked -p apw-core --test watcher --test apple_client
```

常规测试使用本地模拟响应，不添加 `live` 特性。没有验证真实订单提交或付款；选购逻辑的验证边界见 [FORK.md](FORK.md)。

## 许可证

[GPL-3.0-or-later](LICENSE) · [版权与第三方声明](NOTICE)

本项目与 Apple Inc. 无关联，未获其授权或认可。最终库存与取货信息以 Apple 官网为准。
