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

```
用户输入
   │
   ▼
主模型（你当前会话选的模型）
   │ 调 describe_image(image=<URL/base64/文件路径/空>)
   ▼
describe_image 自动分发
   ├─ 空        → 读系统剪贴板 (pngpaste / xclip / PowerShell)
   ├─ URL       → httpx 下载
   ├─ data URI  → 提取 base64
   ├─ 文件路径  → 读文件
   └─ base64    → 直接用
   │
   ▼
视觉模型（qwen3.7-plus 等）→ 返回结构化文字描述
   │
   ▼
主模型基于描述自己推理、回答用户
```

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

## 安装

需要 Python ≥ 3.10。

### 方式 A：一键安装脚本（推荐）

跨平台（macOS / Linux / Windows），自动检测并配置所有已安装的 MCP 客户端：

```bash
cd /path/to/multimodal-mcp
python install.py          # 交互式
python install.py --yes    # 跳过确认
```

脚本自动完成：
1. 检查系统依赖（macOS 提示装 pngpaste，Linux 提示装 xclip）
2. 创建 venv + 装依赖
3. 生成 `.env` 模板
4. 检测已安装的客户端（opencode / Claude Desktop / Claude Code / Cursor / Codex CLI / Windsurf / Cline）
5. 对每个客户端写入 MCP 配置 + 规则文件（幂等，重复跑安全）

跑完后只需：编辑 `.env` 填 key → 重启客户端。

### 方式 B：手动安装

```bash
cd /path/to/multimodal-mcp

# uv (推荐)
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -r requirements.txt

# 或标准 venv + pip
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 配置

只需配一个视觉模型：

```bash
cp .env.example .env
```

```dotenv
VISION_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
VISION_API_KEY=sk-xxxxx
VISION_MODEL=qwen3.7-plus
```

**换模型只改 `.env`，不动代码**。`.env.example` 里给了三种常见预设（阿里云 / OpenAI / 本地 Ollama）。

> BaseURL 只写到 `/v1`，**不要**带 `/chat/completions`，代码自动拼接。

## 自检

用 MCP Inspector 可视化测试（不依赖任何客户端，直接调工具）：

```bash
# 方式 1: npx（无需额外安装，推荐）
npx @modelcontextprotocol/inspector /path/to/multimodal-mcp/.venv/bin/python /path/to/multimodal-mcp/server.py

# 方式 2: mcp CLI（需 pip install "mcp[cli]"）
cd /path/to/multimodal-mcp
mcp dev server.py
```

浏览器打开后：
1. 先调 `multimodal_config_status`，确认 `vision_base_url_set` / `vision_api_key_set` / `vision_model` 三个都就绪
2. 再调 `describe_image`，`image` 留空（读剪贴板）或传一个 URL 测试

## 接入客户端

**方式 A（一键安装脚本）已自动配好所有检测到的客户端**，下面是手动配置参考（换电脑 / 脚本没覆盖的客户端时用）。

通用规则：`command` 用项目 venv 里的 python 绝对路径（避免找不到依赖），`args` 是 `server.py` 绝对路径。不用传 `env`，server 会自动读项目目录的 `.env`。

### opencode

`~/.config/opencode/opencode.json` 的 `mcp` 段：

```jsonc
{
  "mcp": {
    "multimodal": {
      "type": "local",
      "command": ["/path/to/multimodal-mcp/.venv/bin/python", "/path/to/multimodal-mcp/server.py"]
    }
  }
}
```

规则文件：`~/.config/opencode/AGENTS.md`（全局）

### Claude Code

`~/.claude.json` 的 `mcpServers` 段：

```jsonc
{
  "mcpServers": {
    "multimodal": {
      "command": "/path/to/multimodal-mcp/.venv/bin/python",
      "args": ["/path/to/multimodal-mcp/server.py"]
    }
  }
}
```

规则文件：`~/.claude/CLAUDE.md`（全局）

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）：

```jsonc
{
  "mcpServers": {
    "multimodal": {
      "command": "/path/to/multimodal-mcp/.venv/bin/python",
      "args": ["/path/to/multimodal-mcp/server.py"]
    }
  }
}
```

规则文件：项目级 `CLAUDE.md`（Claude Desktop 按项目读）

### Cursor

`~/.cursor/mcp.json` 的 `mcpServers` 段（格式同 Claude Desktop）。

规则文件：`~/.cursor/rules/multimodal.mdc`

### Codex CLI

`~/.codex/config.toml`（TOML 格式）：

```toml
[mcp_servers.multimodal]
command = "/path/to/multimodal-mcp/.venv/bin/python"
args = ["/path/to/multimodal-mcp/server.py"]
```

规则文件：`~/.codex/AGENTS.md`（全局）

### Windsurf / Cline

MCP 配置走各自 UI（Settings > MCP），命令同上。规则文件：`.windsurfrules` / `.clinerules`（项目级）。

> 所有路径用绝对路径。`command` 必须是 venv 里的 python，不是系统的 `python`，否则会缺 `mcp` / `httpx` 等依赖。

## 让 agent 自动调用（关键）

### describe_image（唯一工具，自动分发）

工具描述已写明所有触发条件（包括 `[Image N]` 占位符触发），MCP 协议会把描述发给 LLM，多数客户端会自动调起。

如果你的客户端没自动调，把下面这段加到 agent 的规则文件（opencode: `AGENTS.md` / Claude Code: `~/.claude/CLAUDE.md` / Cursor: `.cursorrules` / Codex: `~/.codex/AGENTS.md`）：

> 1. 当用户提到任何图片（截图 / 照片 / 图表 / URL / 文件路径 / base64）或说"看下我的截图 / 剪贴板里的图 / look at my screenshot"，且当前主模型不支持视觉时，调用 `describe_image` 工具把图片转成文字，再基于描述回答用户。不要拒绝、不要说"我看不到图片"。
> 2. `describe_image` 的 `image` 参数自动分发：有 URL / 文件路径 / base64 → 作为 `image` 传入；用户说"我的截图"但没给地址 → `image` 留空，工具从系统剪贴板读图。
> 3. 当消息里出现 `[Image 1]` / `[Image N]` / `[图片]` / `[Image attachment]` 等占位符（说明用户粘贴了图片附件，但客户端或网关把真实图片替换成占位符），立即调用 `describe_image`，`image` 留空——工具从系统剪贴板读取用户刚截图/粘贴的图片。即使用户没打字、只发了图片，也要这么做。
> 4. 工具返回的是图片文字描述，不是最终答案。拿到描述后由主模型自己推理并回答用户。

一键安装脚本 `install.py` 会自动把这段写进各客户端的规则文件，不用手动加。

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
           （因为用户刚粘贴，图片还在剪贴板里）→ 文字描述 → 主动告诉用户看到了什么
```

这是"只发图不打字"的场景，依赖规则文件里有第 3 条占位符触发提示（`install.py` 已自动写入）。

## 文件结构

```
multimodal-mcp/
├── server.py          # MCP server 全部逻辑（单文件）
├── install.py         # 跨平台一键安装脚本
├── RULES.md           # 跨客户端通用规则模板
├── pyproject.toml
├── requirements.txt
├── .env.example       # 配置模板（含三种预设注释）
├── .gitignore
└── README.md
```

## 故障排查

| 现象 | 排查 |
|---|---|
| `Missing API key for model '...'` | `VISION_BASE_URL` / `VISION_API_KEY` / `VISION_MODEL` 三个都要填 |
| `HTTP 401` | Key 错或没开通该模型 |
| `HTTP 404` | BaseURL 写错，或平台路径不是 `/v1` 结尾 |
| 描述很模糊 | `detail` 设为 `high`；或自定义 `instruction` 指定要提取什么 |
| agent 不自动调 | 工具描述已含触发条件（含 `[Image N]` 占位符）；仍不调则检查客户端是否加载了 MCP、规则文件是否被客户端读取 |

## 限制

- 每次调用走一次视觉模型往返，延迟取决于该模型；不适合超低延迟场景。
- 视觉模型描述什么，主模型就只看什么。极小细节可能丢失——用 `instruction` 把需求写具体。
- 当前实现走 stdio；需要远程多人共用可改成 `streamable_http`。
