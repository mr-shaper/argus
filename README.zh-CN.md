# Argus

> 多源深度调研编排器，将 Perplexity、NotebookLM、X/Twitter、网页爬取和 GitHub 整合进一条 OODC 风格的 Observe 流水线。姐妹技能自备。

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

[Read in English](./README.md)

![ARGUS — 多源调研编排器架构图](./docs/skills-architecture.png)

---

## 为什么选择 Argus

现代 AI 调研任务往往横跨多个信息源——AI 综合摘要、社交信号、学术论文、代码仓库、网页内容——但手动编排这一切既慢又容易出错。Argus 解决的是协调层问题：一条命令并行扇出至多达 6 个通道，管理速率限制和配额，并将结果聚合为结构化 manifest——全程主 AI 不直接读取原始资料正文。它是 OODC（Observe → Orient → Decide → Create）研究工作流中的 **Observe** 阶段，产出一份干净的证据底座，供人主导后续的 Orient/Decide/Create 环节。

---

## 三大支柱

### 🛠️ 技能编排 — 姐妹技能协同

Argus 本身是一个编排层，而非全能 Agent。每个通道依赖一个"姐妹技能"——你自带或自建的独立工具——负责实际的数据采集。Argus 只管调度、限流和结果汇聚。这种设计使得各通道可以独立替换，而不影响整体流水线。

### 🩺 诊断器 — 检测与引导

`probe.py doctor` 是 Argus 的核心健康检查命令，支持两种模式：快速门禁（essential-only）和完整引导检测（交互式，macOS 提供 osascript 弹窗引导）。详见下方 [DOCTOR](#doctor--检测与引导) 节。

> **注意：** 在 macOS 上，部分自动启动尝试可能因 LaunchServices 行为而失败；本工具会检测问题并引导你使用安全的手动命令（参见上手步骤第 1 步中的 `open -na`）。

### 🚦 引导上手 — 首次授权步骤

首次使用时，各通道需要完成一次性授权配置（Cookie、OAuth、CLI 登录等）。Argus 提供结构化的 7 步引导流程。详见下方 [Onboarding](#onboarding--首次授权步骤) 节。

---

## DOCTOR — 检测与引导

`probe.py doctor` 是 Argus 的核心。它有两种模式：

### 模式 1：快速门禁（essential-only）

```bash
python3 scripts/probe.py doctor \
  --essential comet-9223,perplexity-auth,chrome-9222,notebooklm-auth \
  --no-popup
```

只在指定的 essential 通道全部就绪时返回退出码 0。适合 CI / cron 任务 / 调研启动前的就绪门禁检测。

### 模式 2：完整引导检测（交互）

```bash
python3 scripts/probe.py doctor
```

检测全部 6 个通道。在 macOS 上，失败的通道会通过 `osascript` 弹窗给出具体修复指令。你可以：

- 点击"确定"启动自动修复（例如打开 `perplexity-login.py`）
- 点击"取消"跳过并继续
- 修复后再跑 `doctor` 进行验证

`--no-popup` 强制文本输出（CI 友好），`--json` 输出机器可读的 JSON。

### 退出码

| 码 | 含义 |
|----|------|
| 0  | 所有通道就绪 |
| 2  | 部分非 essential 通道失败（可跳过继续） |
| 3  | 至少一个 essential 通道失败（阻断调研） |
| 4  | 配额超限（例如 Perplexity Deep 日配额耗尽） |

---

## Onboarding — 首次授权步骤

首次使用前，按以下步骤完成各通道授权配置。每步只需操作一次，后续调研自动复用。

> ⚠️ **macOS 用户**：请先激活 venv，或使用 `./run-argus.sh` 封装脚本。doctor 命令会从 PATH 调用 `notebooklm` 和 `bird`；通过 `pip` 安装的二进制文件在 `.venv/bin/` 下，不激活 venv 将无法找到。

### Step 1：启动 Comet 浏览器（端口 9223，Perplexity 专属）

Comet 是 Perplexity 专属浏览器。Argus 要求它监听 CDP 端口 9223，以绕过在普通 Chrome 端口上触发的 hcaptcha。

```bash
# macOS：必须使用 open -na（不能用 nohup），因为 LaunchServices 行为限制
# 如果 Comet 已在运行，先 pkill -9
pkill -9 -f "Comet.app" 2>/dev/null || true
sleep 2
open -na "Comet" --args --remote-debugging-port=9223 "--remote-allow-origins=*"
# 注意：--remote-allow-origins=* 必须加引号（防止 zsh glob 展开）

# 确认端口：
curl -s http://localhost:9223/json/version | python3 -m json.tool
```

### Step 2：Perplexity 登录 Cookie

```bash
# 在 Comet 中完成 Perplexity 登录后，提取 Cookie
python3 scripts/perplexity-login.py
# Cookie 将保存至 PERPLEXITY_COOKIES_PATH 指定路径
# 默认: ~/.config/argus/cookies/perplexity.json
```

### Step 3：Chrome 端口 9222 + DevToolsActivePort 补丁（WebAccess 通道）

WebAccess 通道依赖 Chrome 以 CDP 调试模式运行于端口 9222。"connect failed"报错几乎都是 DevToolsActivePort 路径不匹配导致的，与权限无关。

> ⚠️ 每次重启 Comet/Chrome 后，WebSocket UUID 会变化，但 `~/Library/Application Support/Google/Chrome/DevToolsActivePort` 不会自动更新。每次重启浏览器后，必须重新运行 `probe.py doctor` 以修补该文件。

```bash
# macOS：必须使用 open -na（不能用 nohup）
pkill -9 -f "Google Chrome" 2>/dev/null || true
sleep 2
open -na "Google Chrome" --args \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.chrome-debug-profile" \
  "--remote-allow-origins=*"
# 注意：--remote-allow-origins=* 必须加引号（防止 zsh glob 展开）

# 验证连通性
curl -s http://localhost:9222/json/version | python3 -m json.tool
```

### Step 4：WebAccess CDP Proxy

WebAccess 姐妹技能通过 CDP Proxy 转发流量，需要独立启动：

```bash
# 克隆并启动 web-access CDP Proxy（默认端口 3456）
git clone https://github.com/eze-is/web-access
cd web-access
# 按 web-access 的文档启动 proxy
# 验证：
curl -s http://localhost:3456/health
```

### Step 5：NotebookLM OAuth

NotebookLM 通道需要通过 Google OAuth 完成授权，凭证本地缓存后复用：

```bash
pip install notebooklm-py
# 首次运行时会弹出浏览器 OAuth 授权
python3 -c "import notebooklm; notebooklm.authenticate()"
```

### Step 6：Bird CLI（X/Twitter，使用 Chrome 默认 Profile）

Bird 是一个专有 Node.js CLI 二进制文件，**无公开上游**。需自带实现。通道脚本 `bird_batch.py` 是一个 stub，调用 PATH 上的 `bird`；如需贡献公开替代实现，请参阅 `CONTRIBUTING.md` Bird Channel 章节中的接口约定。

将 `bird` 二进制文件放入 PATH 后，先在 Chrome **默认** Profile 中登录 X.com，再验证：

```bash
# 验证 Bird CLI 可访问（已将 bird 二进制放入 PATH 后）
bird whoami
# 应输出你的 X/Twitter 用户名
```

### Step 7：GitHub CLI

```bash
brew install gh
gh auth login
# 验证
gh api user --jq '.login'
```

### Step 8：小红书 MCP 服务（可选，无公开上游）

小红书通道通过 `localhost:18060` 上的 MCP 服务进行查询。该服务为专有组件，无公开源码；你可以：
- 自建本地 MCP 服务，暴露相同的 JSON-RPC schema（`search_feeds`、`get_feed_detail`）
- 跳过此通道：`probe.py run --skip-xhs`

如果你在本地运行该服务：
```bash
# 验证健康状态
curl -s http://localhost:18060/health
# 期望返回：{"success":true,"status":"healthy",...}

# 环境变量（如你的服务使用不同端口/路径可覆盖）
export ARGUS_XHS_MCP_URL="http://localhost:18060/mcp"

# 扫描登录状态（例如 MCP 暴露了 get_login_qrcode 工具）
# 具体首次授权流程请参阅你的本地 MCP 服务文档
```

**无需 SSH** — 仅本机连接。

注意：为防封号，查询之间强制串行限速（`Sem(1)` + 每次 2 秒间隔）。

完成所有步骤后，运行完整的就绪检测确认配置：

```bash
python3 scripts/probe.py doctor
```

---

## 7 通道矩阵

| 通道 | 用途 | 姐妹依赖 | 首次授权 |
|------|------|---------|---------|
| `perplexity_quick` | 快速 AI 综合摘要（< 5 分钟） | 已捆绑 `sister-skills/perplexity-reader/` + Comet :9223 | 步骤 1 + 2 |
| `perplexity_deep` | 深度 AI 长报告（5-10 分钟） | 同上 | 步骤 1 + 2 |
| `nlm` | 多维结构化报告（15-45 分钟，上限 300 来源） | `notebooklm-py` | 步骤 5 |
| `bird` | X/Twitter 帖子搜索 | `bird` CLI（自备 — 无参考实现） | 步骤 6 |
| `webaccess` | 网页爬取 + YouTube URL 发现 | 已捆绑 `sister-skills/chrome-reader/` + `web-access` skill + Chrome CDP | 步骤 3 + 4 |
| `github` | 仓库 / Trending / Issues | `gh` CLI | 步骤 7 |
| `xhs` | 小红书帖子搜索 | 小红书 MCP（自带，本机 :18060） | 步骤 8 |

每个通道均设计为**独立容错**：单个通道失败不会阻断其他通道运行。

---

## 前置依赖（诚实说明）

### OSS 可直接安装（开箱即用）

```bash
# NotebookLM 通道
pip install notebooklm-py

# WebAccess 通道
git clone https://github.com/eze-is/web-access

# GitHub 通道
brew install gh
gh auth login
```

### 已捆绑（无需单独安装）

`perplexity-reader` 和 `chrome-reader` 已捆绑在 `sister-skills/` 子目录中，无需单独安装：

| 依赖 | 通道 | 说明 |
|------|------|------|
| `perplexity-reader` | Perplexity Quick + Deep | 已捆绑于 `sister-skills/perplexity-reader/`，无需单独安装。 |
| `chrome-reader.py` | WebAccess 回退 | 已捆绑于 `sister-skills/chrome-reader/`，无需单独安装。 |

### 需自备（Bring Your Own）

以下依赖由 Argus 调用，但无公开上游。你需要自行提供实现或替代方案：

| 依赖 | 通道 | 说明 |
|------|------|------|
| `bird` CLI | X/Twitter | 封装 X API 或第三方客户端，无确认的公开上游。 |
| `Comet.app` | Perplexity | Perplexity 专属浏览器，用于在 `:9223` 端口规避 hcaptcha。 |

如果你没有上述自备依赖，对应通道在 manifest 中会显示 `status=skipped`，其余通道正常运行。

---

## 快速开始

```bash
git clone <this-repo>.git argus
cd argus

# 安装核心依赖
pip install -r requirements.txt

# 安装姐妹技能（见上方前置依赖）

# 可选：配置环境变量
export PERPLEXITY_COOKIES_PATH="$HOME/.config/argus/cookies/perplexity.json"
export ARGUS_COMET_PORT=9223
export ARGUS_CHROME_PORT=9222

# 验证所有通道（授权 + 连通性检测）
python3 scripts/probe.py doctor --no-popup

# 发起一次完整调研
# （90 分钟预算，15 维 NotebookLM 报告）
python3 scripts/probe.py run \
  --topic "你的调研主题" \
  --dimensions 15 \
  --budget 90m \
  --output ~/argus-output/
```

输出目录将包含：

- `manifest.json` — 各通道状态、URL 列表、摘要索引
- `nlm_report.md` — NotebookLM 结构化报告（如该通道已运行）
- `perplexity_quick.md` / `perplexity_deep.md` — Perplexity 输出
- `github_results.json` — GitHub 搜索结果
- `bird_results.json` — X/Twitter 搜索结果
- `webaccess_results.json` — 网页爬取 URL 与摘要

Argus 支持**断点续跑**：中断后重新执行同一命令，将通过 `manifest.json` 跳过已完成阶段。

---

## CLI 参考

```bash
# 运行完整调研
python3 scripts/probe.py run \
  --topic "调研主题" \
  --dimensions <int>    # NotebookLM 报告维度数，默认 10
  --budget <duration>   # 时间预算，例如 60m / 1h30m，默认 60m
  --output <dir>        # 输出目录，默认 ./argus-output

# 通道就绪检测
python3 scripts/probe.py doctor \
  [--essential <ch1,ch2,...>]   # 仅检测指定 essential 通道
  [--no-popup]                   # 禁用 osascript 弹窗（CI 友好）
  [--json]                       # 输出机器可读 JSON

# 从中断点恢复
python3 scripts/probe.py run \
  --resume ~/argus-output/       # 指向含 manifest.json 的目录

# 单独运行某个通道
python3 scripts/perplexity_quick.py --topic "主题"
python3 scripts/nlm_pipeline.py   --topic "主题" --dimensions 10
python3 scripts/bird_channel.py   --topic "主题" --limit 50
```

---

## 架构

Argus 是一个**多源编排器**，而非端到端 Agent。它只处理 OODC-Observe 阶段——多源证据深度采集——并产出结构化 manifest，供人主导 Orient/Decide/Create 后续环节。

`probe.py` 是主入口，实现 Stage 0–4 状态机：

```
Stage 0：主题接收 + 配额检查
Stage 1：并行通道扇出（Perplexity Quick + Bird + WebAccess + GitHub）
Stage 1.5：NotebookLM 来源摄入（串行，限速）
Stage 2：Perplexity Deep（Quick 完成后启动）
Stage 3：NotebookLM 报告生成
Stage 4：manifest 汇总 + 摘要
```

每个通道脚本（`perplexity_quick.py`、`nlm_pipeline.py` 等）均为独立模块，可单独调用。编排器负责限流和配额管理；各通道之间相互隔离，互不感知。

**核心设计原则**：主 AI 进程绝不直接读取资料正文。它只处理 URL + 标题 + 摘要等元数据。完整原文仅流入 NotebookLM。

---

## 7 铁律

以下铁律已在代码层面强制执行，Fork 或扩展时不得违反：

1. **Perplexity 必须使用 Comet 端口 9223** — Chrome 端口 9222 会触发 hcaptcha。
2. **Chrome DevTools "connect failed" = DevToolsActivePort 路径不匹配** — 不是权限问题，也不是启动参数问题。检查浏览器启动路径。
3. **NotebookLM 硬上限：每个 Notebook 最多 300 个来源** — 超限时 `probe.py` 立即快速失败；禁止尝试通过批量绕过此限制。
4. **主 AI 绝不读取资料正文** — 编排器只处理 URL + 标题 + 摘要。完整原文专属于 NotebookLM。
5. **NotebookLM 报告就绪门禁：所有来源必须确认 `status=ready`** — 禁止在来源未全部就绪前触发报告生成。
6. **通道隔离** — Perplexity 输出和 Bird 输出是独立通道，不得将其导入 NotebookLM。
7. **Perplexity 并发上限：Semaphore(3)** — Quick 和 Deep 共享此上限。超限将触发账号限速风险。

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PERPLEXITY_COOKIES_PATH` | `~/.config/argus/cookies/perplexity.json` | Perplexity 会话 Cookie 路径 |
| `ARGUS_COMET_PORT` | `9223` | Comet 浏览器 CDP 端口（Perplexity 专用） |
| `ARGUS_CHROME_PORT` | `9222` | Chrome CDP 端口（WebAccess 回退） |
| `ARGUS_NLM_DIMENSIONS` | `10` | NotebookLM 报告默认维度数 |
| `ARGUS_BUDGET_MINUTES` | `60` | 会话默认时间预算（分钟） |
| `ARGUS_OUTPUT_DIR` | `./argus-output` | 默认输出目录 |

---

## 贡献

请参阅 [CONTRIBUTING.md](./CONTRIBUTING.md)，了解开发环境搭建、代码风格、PR 流程，以及如何为"自备通道"贡献开放实现。

---

## License

Copyright 2026 Argus Contributors

本项目基于 Apache License 2.0 授权。完整许可证文本见 [LICENSE](./LICENSE)。
