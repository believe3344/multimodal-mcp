# multimodal-mcp

给任意 MCP 客户端（opencode / Claude Code / Claude Desktop / Cursor / Codex CLI / Cherry Studio 等）配上一双"眼睛"，让纯文本主模型也能处理图片。

**核心设计**：MCP 只负责把图片转成文字，**不做任何推理**。推理始终由你当前会话选的主模型完成（glm-5.2 / deepseek-v4-pro / qwen / 任何模型）。

## 单工具自动分发

只暴露一个工具 `describe_image`，根据 `image` 参数自动判断图片来源：

| `image` 参数值 | 行为 |
|---|---|
| 空 / 不传 | 从**系统剪贴板**读图（用户截图后说"看下我的截图"） |
| `http(s)://...` | 下载图片 |
| `data:image/...;base64,...` | 提取 base64 |
| `/path/to/file.png` | 读本地文件 |
| `iVBORw0KGgo...`（raw base64） | 直接用 |

"剪贴板"路径解决了客户端拦截粘贴图片的问题：用户截图后不粘贴到聊天框，而是打字说"看下我的截图"，工具直接从系统剪贴板读图。**跨平台、跨客户端，只改 MCP**。

## 工具

| 工具 | 作用 |
|---|---|
| `describe_image` | 图片（URL / base64 / data URI / 文件路径 / 空=剪贴板）→ 结构化文字描述。OCR + 图表数据 + UI 细节。**不做推理**。 |
| `multimodal_config_status` | 自检 vision 三件套是否配齐（不打印 key）。 |

## 系统依赖（仅"剪贴板"路径需要）

| 平台 | 命令 | 安装 |
|---|---|---|
| macOS | `pngpaste` | `brew install pngpaste` |
| Linux | `xclip` | `apt install xclip` / `pacman -S xclip` |
| Windows | PowerShell（内置） | 无需安装 |

URL / data URI / 文件路径 / base64 四种路径不需要任何系统依赖。

## 配置

**唯一配置方式**：把三个环境变量写进客户端 MCP 配置的 `env` 字段。

| 变量 | 含义 |
|---|---|
| `VISION_BASE_URL` | 视觉模型 API 地址，写到 `/v1` 为止（**不要**带 `/chat/completions`，代码自动拼） |
| `VISION_API_KEY` | 视觉模型 API key |
| `VISION_MODEL` | 视觉模型名（如 `qwen3.7-plus` / `gpt-4o` / `llava:13b`） |

主推理模型不在本 MCP 配置——它是你在客户端会话里选的那个。本 MCP 只负责把图转成文字。

**为什么各客户端的 `command` 写法不一样？**

各客户端的 MCP schema 不同，不是本项目的选择：

- **opencode**：`command` 是数组（可执行文件 + 参数放一起），环境变量字段叫 **`environment`**。
- **Claude Code / Desktop / Cursor**：`command` 是字符串，`args` 是数组，环境变量字段叫 **`env`**。
- **Codex CLI**：TOML 格式，`command` + `args`，`env` 是 inline table。

`install.py` 会按各客户端的正确字段名写入，不用自己记。

## 安装

需要 Python ≥ 3.10（仅 local 模式需要）；**uvx 模式只需 `uv`**（[装 uv](https://docs.astral.sh/uv/getting-started/installation/)）。

### 方式 A：一键安装脚本（推荐）

在仓库目录里运行，自动检测已安装的客户端并写入配置：

```bash
python install.py              # 交互式,auto 模式
python install.py --yes        # 跳过确认

# 带上 vision 配置,一条命令配齐
python install.py \
  --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --api-key sk-xxxxx \
  --model qwen3.7-plus

# 强制 uvx 模式
python install.py --mode uvx --repo git+https://github.com/USER/multimodal-mcp

# 强制 local 模式(clone + venv)
python install.py --mode local
```

脚本对每个检测到的客户端写入 MCP 配置（含凭据，如提供了的话）+ 规则文件，幂等可重复跑。不带 `--base-url` 等参数时只写 command/args，你自己补凭据。

> `--api-key` 会进 shell 历史。介意的话不带这个参数，跑完手动填。

### 方式 B：直接用 uvx（不用 clone 仓库）

装了 `uv` 就能在任何机器上用，不用 clone / venv / pip：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

然后把下面的配置写进客户端（以 opencode 为例，其他客户端见下文）：

```jsonc
{
  "mcp": {
    "multimodal": {
      "type": "local",
      "command": ["uvx", "--from", "git+https://github.com/USER/multimodal-mcp", "multimodal-mcp"],
      "environment": {
        "VISION_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "VISION_API_KEY": "sk-xxxxx",
        "VISION_MODEL": "qwen3.7-plus"
      }
    }
  }
}
```

### 方式 C：手动 local 安装

```bash
cd /path/to/multimodal-mcp
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -r requirements.txt
```

## 自检

用 MCP Inspector 可视化测试（不依赖任何客户端）：

```bash
npx @modelcontextprotocol/inspector /path/to/multimodal-mcp/.venv/bin/python /path/to/multimodal-mcp/server.py
```

浏览器打开后：
1. 先调 `multimodal_config_status`，确认三个值都就绪
2. 再调 `describe_image`，`image` 留空（读剪贴板）或传一个 URL 测试

> Inspector 不会读客户端配置的 `env`。测试前在 shell 里 `export VISION_BASE_URL=...` / `VISION_API_KEY=...` / `VISION_MODEL=...`，子进程会继承。

## 接入客户端

**方式 A（一键安装脚本）已自动配好所有检测到的客户端**，下面是手动配置参考。

### uvx 模式

opencode（`~/.config/opencode/opencode.json`）：

```jsonc
{
  "mcp": {
    "multimodal": {
      "type": "local",
      "command": ["uvx", "--from", "git+https://github.com/USER/multimodal-mcp", "multimodal-mcp"],
      "environment": {
        "VISION_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "VISION_API_KEY": "sk-xxxxx",
        "VISION_MODEL": "qwen3.7-plus"
      }
    }
  }
}
```

Claude Code（`~/.claude.json`）/ Claude Desktop（`~/Library/Application Support/Claude/claude_desktop_config.json`）/ Cursor（`~/.cursor/mcp.json`）：

```jsonc
{
  "mcpServers": {
    "multimodal": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/USER/multimodal-mcp", "multimodal-mcp"],
      "env": {
        "VISION_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "VISION_API_KEY": "sk-xxxxx",
        "VISION_MODEL": "qwen3.7-plus"
      }
    }
  }
}
```

Codex CLI（`~/.codex/config.toml`）：

```toml
[mcp_servers.multimodal]
command = "uvx"
args = ["--from", "git+https://github.com/USER/multimodal-mcp", "multimodal-mcp"]
env = { VISION_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1", VISION_API_KEY = "sk-xxxxx", VISION_MODEL = "qwen3.7-plus" }
```

### local 模式

把 uvx 那行的 command/args 换成 venv 里的 python 绝对路径和 `server.py` 绝对路径：

- opencode：`command` 数组 = `["/path/to/.venv/bin/python", "/path/to/server.py"]`
- Claude / Cursor：`command` = `"/path/to/.venv/bin/python"`，`args` = `["/path/to/server.py"]`
- Codex：`command` = `"/path/to/.venv/bin/python"`，`args` = `["/path/to/server.py"]`

凭据字段照搬 uvx 模式的（opencode 用 `environment`，其他用 `env`）。`command` 必须是 venv 里的 python，不是系统的 `python`，否则会缺 `mcp` / `httpx` 等依赖。

### Windsurf / Cline

MCP 配置走各自 UI（Settings > MCP），`command` / `args` / 凭据字段同上。

### 规则文件

`install.py` 会自动把调用规则写进各客户端的规则文件：

- opencode：`~/.config/opencode/AGENTS.md`（全局）
- Claude Code：`~/.claude/CLAUDE.md`（全局）
- Cursor：`~/.cursor/rules/multimodal.mdc`
- Codex：`~/.codex/AGENTS.md`（全局）
- Windsurf / Cline：项目级 `.windsurfrules` / `.clinerules`
- Claude Desktop：项目级 `CLAUDE.md`

## 让 agent 自动调用

工具描述已写明所有触发条件（包括 `[Image N]` 占位符触发），MCP 协议会把描述发给 LLM，多数客户端会自动调起。如果没自动调，检查客户端是否加载了 MCP、规则文件是否被读取。

规则文件里的核心提示：

1. 用户提到任何图片（截图 / 照片 / 图表 / URL / 文件路径 / base64）或说"看下我的截图"，且主模型不支持视觉时，调 `describe_image`。
2. `image` 参数自动分发：有 URL / 文件路径 / base64 就传进去；用户说"我的截图"但没给地址就留空，工具从系统剪贴板读图。
3. 消息里出现 `[Image 1]` / `[Image N]` / `[图片]` / `[Image attachment]` 等占位符时，立即调 `describe_image`，`image` 留空——图片还在剪贴板里。
4. 工具返回的是文字描述，不是最终答案。主模型基于描述自己推理回答。

## 使用示例

### 场景 1：用户截图后想让你看

```
[用户操作] Cmd+Shift+4 截图到剪贴板（不粘贴到聊天框）
[用户输入] 看下我的截图，里面有个报错
[agent]    调用 describe_image(image=None) → 读剪贴板 → 文字描述 → 基于描述回答
```

### 场景 2：用户给一个图片 URL

```
[用户输入] 描述一下这张图：https://example.com/chart.png
[agent]    调用 describe_image(image="https://example.com/chart.png") → 下载 → 文字描述 → 回答
```

### 场景 3：用户给本地文件路径

```
[用户输入] 看 /tmp/screenshot.png 里的表格
[agent]    调用 describe_image(image="/tmp/screenshot.png") → 读文件 → 文字描述 → 回答
```

### 场景 4：用户粘贴了图片附件（客户端返回占位符）

```
[用户操作] 粘贴图片附件（Cmd+V），不打字直接发送
[客户端]   主模型不支持 image → 拦截或把 image 替换成 [Image 1] 占位符
[agent]    看到 [Image 1] 占位符 → 调用 describe_image(image=None) 读系统剪贴板
           → 文字描述 → 主动告诉用户看到了什么
```

## 文件结构

```
multimodal-mcp/
├── server.py          # MCP server 全部逻辑（单文件）
├── install.py         # 跨平台一键安装脚本
├── RULES.md           # 跨客户端通用规则模板
├── pyproject.toml
├── requirements.txt
├── .gitignore
└── README.md
```

## 故障排查

| 现象 | 排查 |
|---|---|
| `Missing API key for model '...'` | 客户端配置的 `env` 里三个 `VISION_*` 没填齐 |
| `HTTP 401` | Key 错或没开通该模型 |
| `HTTP 404` | BaseURL 写错，或平台路径不是 `/v1` 结尾 |
| 描述很模糊 | `detail` 设为 `high`；或自定义 `instruction` 指定要提取什么 |
| agent 不自动调 | 检查客户端是否加载了 MCP、规则文件是否被读取 |

## 限制

- 每次调用走一次视觉模型往返，延迟取决于该模型；不适合超低延迟场景。
- 视觉模型描述什么，主模型就只看什么。极小细节可能丢失——用 `instruction` 把需求写具体。
- 当前实现走 stdio；需要远程多人共用可改成 `streamable_http`。
