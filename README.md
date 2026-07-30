# multimodal-mcp

给任意 MCP 客户端配上一双"眼睛"，让纯文本主模型也能处理图片。

**核心设计**：MCP 只把图片转成文字，**不做推理**。推理由你当前会话选的主模型完成（glm-5.2 / deepseek / qwen / 任何模型）。

## 工具总览

### 识别工具

| 工具 | 用途 |
|---|---|
| `describe_image` | 单图识别，支持 URL / data URI / 文件路径 / base64 / 系统剪贴板，返回 `image_id` |
| `describe_images` | 1-8 张图片联合识别或比较，保持传入顺序 |
| `describe_pdf` | PDF 识别：数字页直接提取文本，扫描页走视觉识别，默认前 20 页 |
| `ask_image` | 使用 `image_id` 对已识别的图片追问，不重复传图 |

### 附件工具

| 工具 | 用途 |
|---|---|
| `describe_pasted_images` | 按数量读取 OpenCode 附件缓存（`~/.cache/opencode/multimodal-attachments`） |
| `describe_claude_pasted_images` | 按数量读取 Claude Code 附件缓存（`~/.claude/image-cache/<session-id>/`），自动通过 `CLAUDE_CODE_SESSION_ID` 定位会话 |

### 后台任务工具

| 工具 | 用途 |
|---|---|
| `start_recognition` | 启动后台识别任务（支持 image / images / pdf / image_id），立即返回 `job_id` |
| `get_recognition` | 查询任务进度，默认即时快照（`wait_seconds=0`），支持最多 50 秒长等待 |
| `cancel_recognition` | 取消运行中或排队中的后台任务 |

### 管理工具

| 工具 | 用途 |
|---|---|
| `multimodal_cache_status` | 查看描述缓存命中率、图片会话与任务管理器状态 |
| `clear_multimodal_state` | 清理描述缓存、图片会话或全部状态 |
| `multimodal_config_status` | 自检 `PROVIDER` / `BASE_URL` / `API_KEY` / `MODEL_NAME` 是否已配置且合法（不暴露 key 值） |

`describe_image` 的 `image` 参数自动判断图片来源：

| `image` 参数 | 行为 |
|---|---|
| 空 | 从**系统剪贴板**读图（截图后说"看下我的截图"） |
| `http(s)://` | 下载 |
| `data:image/...;base64,...` | 提取 base64 |
| `/path/to/file` | 读本地文件 |
| raw base64 | 直接用 |

返回结构化文字描述（OCR + 图表数据 + UI 细节），主模型基于描述自己推理。

**稳定性处理**：所有图片来源先嗅探真实类型（非图片直接拒绝，不会把任意文件内容发给视觉 API）；超过 2048px 或 4MB 的图自动缩到长边 1568px 并重编码为 JPEG——视网膜全屏截图从几 MB 压到几百 KB，避免上游体积限制和超时。单源上限 64MB。每个阶段都写 stderr 日志（分辨率、体积、耗时），排查时看 MCP 服务器 stderr 输出即可。

**缓存与生命周期**：描述缓存 TTL 1 小时，图片会话 TTL 30 分钟。数据仅在进程内存中，重启即清空。PDF 每次最多处理 20 页，多图最多 8 张。

**同步识别**：普通 `describe_*` 和 `ask_image` 工具直接等待视觉模型完成并返回最终文字描述，不会因超过一定时长就提前返回 `job_id`。等待是一个 `await`，不轮询、不产生额外视觉请求。网络/状态瞬态错误最多自动重试两次。

**后台识别（opt-in）**：`start_recognition` 立即返回 job 快照，`get_recognition` 可按需查询进度（最多等待 50 秒），`cancel_recognition` 可取消已提交任务。任务结果保留 1 小时，进程重启后失效。

**OpenCode 用户须知**：OpenCode 默认约 60 秒 MCP 请求超时，须在配置中将 `mcp.multimodal.timeout` 设为 `960000`（毫秒），覆盖服务端 900 秒任务总超时。运行 `install.py` 可自动写入此值。

"剪贴板"路径解决客户端拦截粘贴图片的问题：截图后不粘贴到聊天框，打字说"看下我的截图"，工具直接读剪贴板。跨平台跨客户端。

## 项目架构

```
multimodal-mcp/
├── server.py              # MCP 服务入口，注册全部 12 个工具，图片来源解析与裁剪
├── recognition.py         # RecognitionRequest 与 RecognitionRunner，编排识别流程
├── providers.py           # openai / anthropic 双 provider 适配（请求构造、响应提取）
├── state.py               # 内存缓存：描述缓存（TTL 1h）、图片会话（TTL 30min）、LRU 淘汰
├── jobs.py                # 异步任务管理器：去重、状态追踪、超时、取消、容量限制
├── attachments.py         # OpenCode 附件缓存解析（~/.cache/opencode/multimodal-attachments）
├── claude_attachments.py  # Claude Code 附件缓存解析（~/.claude/image-cache/<session-id>/）
├── pdf_support.py         # PDF 页选择、文本提取 / 扫描页渲染（PyMuPDF）
├── install.py             # 跨平台安装脚本：检测客户端、写入 MCP 配置与规则文件
└── tests/                 # 15 个测试文件，覆盖全部模块
```

| 模块 | 职责 |
|---|---|
| `server.py` | FastMCP 服务入口。定义全部 12 个 MCP 工具；图片来源自动分发（URL 下载 / data URI 解码 / 文件读取 / 剪贴板读取）；图片真实类型嗅探、尺寸裁剪（超过 2048px 或 4MB 缩至长边 1568px）；上游并发限制与重试 |
| `recognition.py` | `RecognitionRequest`（SHA-256 去重键）+ `RecognitionRunner`：单图 / 多图 / image_id / PDF 四种识别流程编排 |
| `providers.py` | Provider 规范化与校验；openai（`/chat/completions`，Bearer）和 anthropic（`/v1/messages`，x-api-key）的请求体构造与响应文本提取 |
| `state.py` | 进程内存缓存。描述缓存 TTL 1 小时，图片会话 TTL 30 分钟，均带 LRU 淘汰和字节上限；命中/未命中计数 |
| `jobs.py` | 异步 JobManager。提交去重（同参数复用结果）、状态机（queued → processing → completed/partial/failed/cancelled）、单元追踪、超时、取消、TTL 清理 |
| `attachments.py` | 遍历 `~/.cache/opencode/multimodal-attachments`，按后缀过滤、按 mtime 选最新 N 张、恢复粘贴顺序 |
| `claude_attachments.py` | 通过 `CLAUDE_CODE_SESSION_ID` 环境变量定位 `~/.claude/image-cache/<session-id>/`，按数字文件名排序（`1.png`→`[Image 1]`），带路径穿越防护和 newest-session 兜底 |
| `pdf_support.py` | PyMuPDF 封装：页码选择解析（`"1-3,5"`）、auto/text/vision 模式切换、扫描页 PNG 渲染，默认上限 20 页 |
| `install.py` | 自动检测已安装客户端（opencode / Claude Desktop / Claude Code / Cursor / Codex / Windsurf / Cline），写入 MCP 配置 + 规则文件；支持 uvx / local 两种运行模式 |

## 系统依赖

仅"剪贴板"路径需要：

| 平台 | 命令 | 安装 |
|---|---|---|
| macOS | `pngpaste` | `brew install pngpaste` |
| Linux | `xclip` | `apt install xclip` |
| Windows | PowerShell | 内置 |

URL / data URI / 文件路径 / base64 四种路径无依赖。

## 安装与配置

需要 Python ≥ 3.10（仅 local 模式）；uvx 模式只需 [uv](https://docs.astral.sh/uv/getting-started/installation/)。

### 凭据

四个环境变量，写进客户端 MCP 配置的凭据字段：

| 变量 | 含义 |
|---|---|
| `PROVIDER` | 视觉 API 提供方：`openai`（默认）或 `anthropic` |
| `BASE_URL` | 视觉模型 API 根地址。`PROVIDER=openai` 时填 `/v1` 根；`PROVIDER=anthropic` 时填服务根，例如 `https://api.anthropic.com` |
| `API_KEY` | API key |
| `MODEL_NAME` | 模型名（`qwen3.7-plus` / `gpt-4o` / `llava:13b` / `gpt-5.4` 等） |

主推理模型不在这里配——它是你客户端会话里选的那个。

- `PROVIDER=openai`：服务端请求 `BASE_URL + /chat/completions`
- `PROVIDER=anthropic`：服务端请求 `BASE_URL + /v1/messages`

> 各客户端的凭据字段名不一样：opencode 叫 `environment`，Claude / Cursor / Codex 叫 `env`。`install.py` 会自动用对的字段名。

### 方式 A：一键脚本（推荐）

在仓库目录里运行，自动检测已装客户端并写入配置 + 规则文件，幂等可重复跑：

```bash
python install.py              # 交互式
python install.py --yes        # 跳过确认

# 带凭据,一条命令配齐
python install.py \
  --provider openai \
  --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --api-key sk-xxxxx \
  --model qwen3.7-plus

python install.py \
  --provider anthropic \
  --base-url https://api.anthropic.com \
  --api-key sk-ant-xxxxx \
  --model claude-3-7-sonnet-latest

# 强制 uvx / local 模式
python install.py --mode uvx --repo git+https://github.com/believe3344/multimodal-mcp
python install.py --mode local
```

跑完重启客户端即可。`--api-key` 会进 shell 历史，介意就跑完手动填。

### 方式 B：手动配置

不用 install.py，按下面格式写进各客户端配置。两种运行模式：

- **uvx**（不用 clone）：command 跑 `uvx --from git+URL multimodal-mcp`
- **local**（clone + venv）：command 跑 venv 里的 python + `server.py`

**opencode**（`~/.config/opencode/opencode.json`）— `command` 是数组，凭据字段叫 `environment`。OpenCode 需设 `timeout` 覆盖默认约 60 秒限制：

```jsonc
{
  "mcp": {
    "multimodal": {
      "type": "local",
      "command": ["uvx", "--from", "git+https://github.com/believe3344/multimodal-mcp", "multimodal-mcp"],
      "timeout": 960000,
      "environment": {
        "PROVIDER": "openai",
        "BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "API_KEY": "sk-xxxxx",
        "MODEL_NAME": "qwen3.7-plus"
      }
    }
  }
}
```

**Claude Code / Desktop / Cursor**（`~/.claude.json` / `~/Library/Application Support/Claude/claude_desktop_config.json` / `~/.cursor/mcp.json`）— `command` 字符串 + `args` 数组，凭据字段叫 `env`：

```jsonc
{
  "mcpServers": {
    "multimodal": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/believe3344/multimodal-mcp", "multimodal-mcp"],
      "env": {
        "PROVIDER": "openai",
        "BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "API_KEY": "sk-xxxxx",
        "MODEL_NAME": "qwen3.7-plus"
      }
    }
  }
}
```

**Codex CLI**（`~/.codex/config.toml`）— TOML，`env` 是 inline table：

```toml
[mcp_servers.multimodal]
command = "uvx"
args = ["--from", "git+https://github.com/believe3344/multimodal-mcp", "multimodal-mcp"]
env = { PROVIDER = "openai", BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1", API_KEY = "sk-xxxxx", MODEL_NAME = "qwen3.7-plus" }
```

**local 模式**：把上面 uvx 的 command/args 换成 venv python + `server.py` 绝对路径，凭据字段不变（opencode 仍 `environment`，其他仍 `env`）。`command` 必须是 venv 里的 python，否则缺 `mcp` / `httpx` 依赖。准备 venv：

```bash
cd /path/to/multimodal-mcp
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -r requirements.txt
```

**Windsurf / Cline**：MCP 配置走各自 UI（Settings > MCP），格式同上。

### 规则文件

`install.py` 会自动把"何时调 `describe_image`"的规则写进各客户端规则文件（opencode `AGENTS.md` / Claude `CLAUDE.md` / Cursor `.mdc` / Codex `AGENTS.md` / Windsurf `.windsurfrules` / Cline `.clinerules`）。手动配置时需自行添加，模板见 `RULES.md`。

## 测试

重启客户端后：
1. 调 `multimodal_config_status`，确认 `PROVIDER` 正确且凭据都 set
2. 调 `describe_image`，`image` 留空（读剪贴板）或传 URL

或用 MCP Inspector 独立测试（不依赖客户端，需先在 shell 设置 `PROVIDER` / `BASE_URL` / `API_KEY` / `MODEL_NAME`）：

```bash
npx @modelcontextprotocol/inspector .venv/bin/python server.py
```

## 使用示例

### 截图

```
[用户] Cmd+Shift+4 截图,然后说"看下我的截图"
[agent] describe_image(image=None) → 读剪贴板 → 文字描述 → 回答
```

### 图片 URL

```
[用户] 描述这张图:https://example.com/chart.png
[agent] describe_image(image="https://...") → 下载 → 描述 → 回答
```

### 本地文件

```
[用户] 看 /tmp/screenshot.png 里的表格
[agent] describe_image(image="/tmp/screenshot.png") → 读文件 → 描述 → 回答
```

### 多图同步识别

```
[用户] 识别这三张图片
[agent] describe_images(...) → 持续等待识别 → 返回最终文字描述 → 同一回合回答
```

### 粘贴附件（占位符）

部分客户端在把图片粘贴到对话框后，会把原始图片变成 `[Image 1]` 占位符且不保留系统剪贴板数据。解析顺序：

1. 若消息里出现 `[Image: source: /绝对路径/文件.png]` 格式的路径标记，直接提取绝对路径传给 `describe_image` 或 `describe_images`。
2. 否则，若消息里出现 `[Multimodal attachment paths: ...]` 标记，直接把标记中的路径传给 `describe_image` 或 `describe_images`。
3. 否则，若无路径标记但有 N 个占位符，按客户端选择工具：
   - **Claude Code**：调用 `describe_claude_pasted_images(count=N)`。Claude Code 把粘贴图片存在 `~/.claude/image-cache/<session-id>/<N>.png`，数字文件名就是占位符序号；工具通过 server 进程环境变量 `CLAUDE_CODE_SESSION_ID` 精确定位当前会话目录（env 缺失时退化为最新会话目录），无需任何配置。
   - **OpenCode**：调用 `describe_pasted_images(count=N)` 读取 `~/.cache/opencode/multimodal-attachments` 中的最新 N 张附件；工具会恢复原始粘贴顺序。
4. 若附件目录为空或数量不足，回退为 `describe_image(image="")` 读取系统剪贴板。

OpenCode 侧的附件缓存由插件 `~/.config/opencode/plugins/multimodal-attachment-bridge.js` 写入，OpenCode 会自动加载。临时图片仅当前用户可读，1 小时后在下次 OpenCode 启动时清理。

## 故障排查

| 现象 | 排查 |
|---|---|---|
| `Missing API key` | 凭据字段里 `PROVIDER` / `API_KEY` / `BASE_URL` / `MODEL_NAME` 没填齐，或字段写错（opencode 是 `environment` 不是 `env`） |
| `HTTP 401` | Key 错或没开通该模型 |
| `HTTP 404` | `PROVIDER=openai` 时 `BASE_URL` 不是 `/v1` 根，或 `PROVIDER=anthropic` 时 `BASE_URL` 误填成 `/v1/messages` 之类的完整路径 |
| 大图超时 / 间歇失败 | 已在服务端自动压缩；仍失败就看 MCP 服务器 stderr 日志里的体积与耗时 |
| OpenCode 约 60 秒超时 | 未配 `timeout` 字段。运行 `install.py` 自动写入 `960000` ms，或手动在 `opencode.json` 的 `mcp.multimodal` 下加 `"timeout": 960000` |
| Claude Code 工具调用超时 | 未配 `MCP_TOOL_TIMEOUT`。运行 `install.py` 自动写入 `~/.claude/settings.json` 的 `env.MCP_TOOL_TIMEOUT=960000`，或手动添加 |
| `not a supported image` | 输入不是图片文件（只支持 PNG/JPEG/GIF/WebP/BMP） |
| `clipboard has no image` | 截图后别再复制其他内容；macOS 用 Cmd+Ctrl+Shift+4 才直接进剪贴板 |
| 描述模糊 | `detail` 设 `high`，或自定义 `instruction` |
| agent 不自动调 | 检查客户端是否加载 MCP、规则文件是否被读取 |

## 限制

- 每次调用一次视觉模型往返，延迟取决于该模型。
- 视觉模型描述什么，主模型就只看什么。极小细节可能丢失——用 `instruction` 写具体。
- 走 stdio；远程多人共用可改 `streamable_http`。
