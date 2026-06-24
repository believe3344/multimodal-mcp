<!-- multimodal-mcp rules start -->
## multimodal-mcp 图片识别规则

1. 当用户提到任何图片（截图 / 照片 / 图表 / URL / 文件路径 / base64）或说"看下我的截图 / 刚截的图 / 剪贴板里的图 / look at my screenshot"，且当前主模型不支持视觉时，调用 `describe_image` MCP 工具把图片转成文字，再基于描述回答用户。不要拒绝、不要说"我看不到图片"。

2. `describe_image` 的 `image` 参数自动分发：传 http(s) URL 会下载；传 data URI 会提取 base64；传本地文件路径会读取；传 raw base64 会直接用；留空（用户截图场景）会从系统剪贴板读取。根据用户给的信息决定传什么：有地址传地址，用户说"我的截图"但没给地址就留空。

3. 当消息里出现 `[Image 1]`、`[Image N]`、`[图片]`、`[Image attachment]` 等占位符（说明用户粘贴了图片附件，但客户端或网关把真实图片替换成占位符），立即调用 `describe_image`，`image` 留空——工具从系统剪贴板读取用户刚截图/粘贴的图片。即使用户没打字、只发了图片，也要这么做。拿到描述后主动告诉用户你看到了什么，并询问需要做什么。

4. 工具返回的是图片文字描述，不是最终答案。拿到描述后由主模型自己推理并回答用户。
<!-- multimodal-mcp rules end -->

