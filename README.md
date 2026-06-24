# multimodal-mcp

给任意 MCP 客户端配上一双"眼睛"，让纯文本主模型也能处理图片。

**核心设计**：MCP 只把图片转成文字，**不做推理**。推理由你当前会话选的主模型完成（glm-5.2 / deepseek / qwen / 任何模型）。

## 工作原理

一个工具 `describe_image`，根据 `image` 参数自动判断图片来源：

| `image` 参数 | 行为 |
|---|---|
| 空 | 从**系统剪贴板**读图（截图后说"看下我的截图"） |
| `http(s)://` | 下载 |
| `data:image/...;base64,...` | 提取 base64 |
| `/path/to/file` | 读本地文件 |
| raw base64 | 直接用 |

返回结构化文字描述（OCR + 图表数据 + UI 细节），主模型基于描述自己推理。

另一个工具 `multimodal_config_status` 自检三个 vision 变量是否配齐（不打印 key）。

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

三个环境变量，写进客户端 MCP 配置的凭据字段：

| 变量 | 含义 |
|---|---|
| `VISION_BASE_URL` | 视觉模型 API 地址，到 `/v1` 为止（**不带** `/chat/completions` 或 `/responses`，代码按 style 自动拼） |
| `VISION_API_KEY` | API key |
| `VISION_MODEL` | 模型名（`qwen3.7-plus` / `gpt-4o` / `llava:13b` / `gpt-5.4` 等） |
| `VISION_API_STYLE` | API 风格：`chat`（默认，`/chat/completions`）或 `responses`（GPT-5 等新模型，`/responses`） |

主推理模型不在这里配——它是你客户端会话里选的那个。

> 各客户端的凭据字段名不一样：opencode 叫 `environment`，Claude / Cursor / Codex 叫 `env`。`install.py` 会自动用对的字段名。

### 方式 A：一键脚本（推荐）

在仓库目录里运行，自动检测已装客户端并写入配置 + 规则文件，幂等可重复跑：

```bash
python install.py              # 交互式
python install.py --yes        # 跳过确认

# 带凭据,一条命令配齐
python install.py \
  --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --api-key sk-xxxxx \
  --model qwen3.7-plus

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
        "VISION_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "VISION_API_KEY": "sk-xxxxx",
        "VISION_MODEL": "qwen3.7-plus"
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
        "VISION_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "VISION_API_KEY": "sk-xxxxx",
        "VISION_MODEL": "qwen3.7-plus"
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
env = { VISION_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1", VISION_API_KEY = "sk-xxxxx", VISION_MODEL = "qwen3.7-plus" }
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
1. 调 `multimodal_config_status`，确认三个变量都 set
2. 调 `describe_image`，`image` 留空（读剪贴板）或传 URL

或用 MCP Inspector 独立测试（不依赖客户端，需先在 shell `export VISION_*` 三个变量）：

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

客户端把粘贴的图片替换成 `[Image 1]` 占位符时，agent 按规则会调 `describe_image`、`image` 留空读剪贴板（图片还在剪贴板里）。

## 故障排查

| 现象 | 排查 |
|---|---|
| `Missing API key` | 凭据字段里三个 `VISION_*` 没填齐（opencode 是 `environment` 不是 `env`） |
| GPT-5 系列超时 / 404 | 设 `VISION_API_STYLE=responses`（走 `/responses`，默认是 `/chat/completions`） |
| `HTTP 401` | Key 错或没开通该模型 |
| `HTTP 404` | BaseURL 不是 `/v1` 结尾，或 style 选错 |
| 描述模糊 | `detail` 设 `high`，或自定义 `instruction` |
| agent 不自动调 | 检查客户端是否加载 MCP、规则文件是否被读取 |

## 限制

- 每次调用一次视觉模型往返，延迟取决于该模型。
- 视觉模型描述什么，主模型就只看什么。极小细节可能丢失——用 `instruction` 写具体。
- 走 stdio；远程多人共用可改 `streamable_http`。
