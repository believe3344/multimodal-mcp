# multimodal-mcp

给任意 MCP 客户端配上一双"眼睛"，让纯文本主模型也能处理图片。

**核心设计**：MCP 只把图片转成文字，**不做推理**。推理由你当前会话选的主模型完成（glm-5.2 / deepseek / qwen / 任何模型）。

## 工具总览

| 工具 | 用途 |
|---|---|
| `describe_image` | 单图识别，并返回短期 `image_id` |
| `describe_images` | 1-8 张图片联合识别或比较 |
| `describe_pdf` | 数字 PDF 文本提取、扫描页视觉识别 |
| `ask_image` | 使用 `image_id` 对图片继续提问 |
| `describe_pasted_images` | 按数量读取 OpenCode 最近粘贴的附件图片 |
| `start_recognition` | 启动图片、多图、PDF 或 image_id 后台识别 |
| `get_recognition` | 查询任务进度，支持最多 50 秒长等待 |
| `cancel_recognition` | 取消后台识别任务 |
| `multimodal_cache_status` | 查看缓存命中、任务与内存占用 |
| `clear_multimodal_state` | 清理描述缓存或短期图片会话 |

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

**后台识别**：现有 `describe_*` 工具最多同步等待 10 秒。未完成时返回 `job_id`，任务仍在后台运行；规则要求仅调用一次 `get_recognition(job_id, wait_seconds=50)`。若仍未完成，等待用户下一次追问后再查询，避免短间隔轮询放大主模型 token 消耗。任务结果保留 1 小时，进程重启后失效。最多并发两个视觉请求，瞬态错误自动重试两次。

`multimodal_config_status` 自检四个配置字段是否合理（不打印 key）。

"剪贴板"路径解决客户端拦截粘贴图片的问题：截图后不粘贴到聊天框，打字说"看下我的截图"，工具直接读剪贴板。跨平台跨客户端。

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

**opencode**（`~/.config/opencode/opencode.json`）— `command` 是数组，凭据字段叫 `environment`：

```jsonc
{
  "mcp": {
    "multimodal": {
      "type": "local",
      "command": ["uvx", "--from", "git+https://github.com/believe3344/multimodal-mcp", "multimodal-mcp"],
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

### 粘贴附件（占位符）

部分客户端在把图片粘贴到对话框后，会把原始图片变成 `[Image 1]` 占位符且不保留系统剪贴板数据。对 OpenCode：

1. 若消息里出现 `[Multimodal attachment paths: ...]` 标记，直接把标记中的路径传给 `describe_image` 或 `describe_images`。
2. 若无路径标记但有 N 个占位符，调用 `describe_pasted_images(count=N)` 读取 `~/.cache/opencode/multimodal-attachments` 中的最新 N 张附件；工具会恢复原始粘贴顺序。
3. 若附件目录为空或数量不足，回退为 `describe_image(image="")` 读取系统剪贴板。

插件文件位于 `~/.config/opencode/plugins/multimodal-attachment-bridge.js`，OpenCode 会自动加载。临时图片仅当前用户可读，1 小时后在下次 OpenCode 启动时清理。

## 故障排查

| 现象 | 排查 |
|---|---|
| `Missing API key` | 凭据字段里 `PROVIDER` / `API_KEY` / `BASE_URL` / `MODEL_NAME` 没填齐，或字段写错（opencode 是 `environment` 不是 `env`） |
| `HTTP 401` | Key 错或没开通该模型 |
| `HTTP 404` | `PROVIDER=openai` 时 `BASE_URL` 不是 `/v1` 根，或 `PROVIDER=anthropic` 时 `BASE_URL` 误填成 `/v1/messages` 之类的完整路径 |
| 大图超时 / 间歇失败 | 已在服务端自动压缩；仍失败就看 MCP 服务器 stderr 日志里的体积与耗时 |
| `not a supported image` | 输入不是图片文件（只支持 PNG/JPEG/GIF/WebP/BMP） |
| `clipboard has no image` | 截图后别再复制其他内容；macOS 用 Cmd+Ctrl+Shift+4 才直接进剪贴板 |
| 描述模糊 | `detail` 设 `high`，或自定义 `instruction` |
| agent 不自动调 | 检查客户端是否加载 MCP、规则文件是否被读取 |

## 限制

- 每次调用一次视觉模型往返，延迟取决于该模型。
- 视觉模型描述什么，主模型就只看什么。极小细节可能丢失——用 `instruction` 写具体。
- 走 stdio；远程多人共用可改 `streamable_http`。
