<!-- multimodal-mcp rules start -->
## multimodal-mcp 图片识别规则

1. 当用户提到任何图片（截图 / 照片 / 图表 / URL / 文件路径 / base64）或说"看下我的截图 / 刚截的图 / 剪贴板里的图 / look at my screenshot"，且当前主模型不支持视觉时，调用 `describe_image` MCP 工具把图片转成文字，再基于描述回答用户。不要拒绝、不要说"我看不到图片"。

2. `describe_image` 的 `image` 参数自动分发：传 http(s) URL 会下载；传 data URI 会提取 base64；传本地文件路径会读取；传 raw base64 会直接用；留空（用户截图场景）会从系统剪贴板读取。根据用户给的信息决定传什么：有地址传地址，用户说"我的截图"但没给地址就留空。

3. 当消息里出现 `[Image 1]`、`[Image N]`、`[图片]`、`[Image attachment]` 等占位符时，按以下顺序选择图片来源：
    - 若消息中带有 `[Image: source: /绝对路径/文件.png]` 格式的路径标记，直接提取绝对路径，单图传 `describe_image`，多图传 `describe_images`；不要调 `describe_claude_pasted_images` 也不读剪贴板。
    - 否则，若消息中有 `[Multimodal attachment paths: ...]` 标记，按标记中的本地路径顺序，单图传 `describe_image`，多图传 `describe_images`。
    - 否则，若消息中出现 Reasonix 附件标记（`@.reasonix/attachments/...` 路径，或 `[image attachment available at @.reasonix/attachments/<文件>; ...]`），提取其中的相对路径，基于项目根目录解析为绝对路径，单图传 `describe_image`，多图传 `describe_images`；不要读剪贴板。
    - 否则，若无路径标记但出现 N 个占位符：在 Claude Code 中调用 `describe_claude_pasted_images(count=N)`（自动定位当前会话的 ~/.claude/image-cache/<session-id>/ 目录，数字文件名即粘贴顺序）；在 OpenCode 中调用 `describe_pasted_images(count=N)` 读取 OpenCode 附件目录的最新 N 张图片，并恢复原始粘贴顺序。
    - 若对应工具返回识别失败错误（目录不存在、图片数量不足等），回退调用 `describe_image`，`image` 留空读取系统剪贴板。
    即使用户没打字、只发了图片，也要这么做。拿到描述后主动告诉用户你看到了什么，并询问需要做什么。

4. 工具返回的是图片文字描述，不是最终答案。拿到描述后由主模型自己推理并回答用户。

5. 用户一次提供多张图片、要求比较图片或连续阅读多张截图时，调用 `describe_images`；保持用户提供的图片顺序。

6. 用户提供 PDF 时调用 `describe_pdf`。数字 PDF 直接提取文字，扫描页自动视觉识别；需要指定页码时传 `pages="1-3,5"`。

7. `describe_image`、`describe_images`、`describe_pasted_images` 和扫描 PDF 会返回 `image_id`。用户对刚才的图片继续追问时，优先调用 `ask_image(image_id, question)`，不要要求用户重新上传。

8. `image_id` 只在当前 MCP 进程内短期有效；过期后重新调用原识别工具。不要把 `image_id` 当永久文件标识。

9. 普通 describe 工具（`describe_image`、`describe_images`、`describe_pasted_images`、`describe_claude_pasted_images`、`describe_pdf`、`ask_image`）会等待识别完成并直接返回最终结果。不要因为工具等待时间较长而重复调用 describe 工具、改用 `get_recognition`、回退剪贴板或让用户稍后追问。只有明确调用 `start_recognition` 启动后台任务后，才使用 `get_recognition` 查询该任务。

10. 当 PDF 任务返回 partial 结果时，用已完成页面回答用户问题，并清晰报告失败页码。只在用户要求时重试失败页。
<!-- multimodal-mcp rules end -->
