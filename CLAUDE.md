<!-- multimodal-mcp rules start -->
## multimodal-mcp 图片识别规则

1. 当用户提到任何图片（截图 / 照片 / 图表 / URL / 文件路径 / base64）或说"看下我的截图 / 刚截的图 / 剪贴板里的图 / look at my screenshot"，且当前主模型不支持视觉时，调用 `describe_image` MCP 工具把图片转成文字，再基于描述回答用户。不要拒绝、不要说"我看不到图片"。

2. `describe_image` 的 `image` 参数自动分发：传 http(s) URL 会下载；传 data URI 会提取 base64；传本地文件路径会读取；传 raw base64 会直接用；留空（用户截图场景）会从系统剪贴板读取。根据用户给的信息决定传什么：有地址传地址，用户说"我的截图"但没给地址就留空。

3. 当消息里出现 `[Image 1]`、`[Image N]`、`[图片]`、`[Image attachment]` 等占位符时，按以下顺序选择图片来源：
    a. 若同时有 `[Multimodal attachment paths: ...]` 标记，用标记内的文件路径按顺序调用 `describe_image` 或 `describe_images`；不要读剪贴板。
    b. 否则，统计占位符数量 N，调用 `describe_pasted_images(count=N)` 从粘贴图片暂存队列获取。
    c. 若暂存队列不可用或返回数量不足的错误，再调用 `describe_image` 留空读系统剪贴板兜底。
    即使用户没打字、只发了图片，也要这么做。拿到描述后主动告诉用户你看到了什么，并询问需要做什么。

4. 工具返回的是图片文字描述，不是最终答案。拿到描述后由主模型自己推理并回答用户。

5. 当 `describe_pasted_images` 因 watcher 不可用而失败时，优先调用 `start_paste_watcher` 工具；该工具会在 macOS 上自动补齐 watcher 二进制和 plist 后再启动。只有自动准备失败时，才提示用户安装 Xcode Command Line Tools 或检查本地源码。不再需要时调用 `stop_paste_watcher` 停止。

6. 普通 describe 工具（`describe_image`、`describe_images`、`describe_pasted_images`、`describe_pdf`、`ask_image`）会等待识别完成并直接返回最终结果。不要因为工具等待时间较长而重复调用 describe 工具、改用 `get_recognition`、回退剪贴板或让用户稍后追问。只有明确调用 `start_recognition` 启动后台任务后，才使用 `get_recognition` 查询该任务。
<!-- multimodal-mcp rules end -->
