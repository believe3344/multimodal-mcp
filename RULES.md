# Multimodal Image Rules (cross-client template)

Copy this content into your client's rules file so the agent knows when to call
the `describe_image` MCP tool. See the bottom of this file for where each
client reads its rules.

## Rules

1. When the user mentions any image (screenshot / photo / chart / URL / file
   path / base64) or says things like "看下我的截图 / 刚截的图 / 剪贴板里的图 /
   look at my screenshot", and the current main model has no vision, call the
   `describe_image` MCP tool to convert the image to text, then answer the
   user based on that text. Never refuse or say "I can't see images".

2. The `describe_image` tool's `image` argument auto-dispatches by content:
   - http(s) URL -> downloaded
   - data URI -> base64 extracted
   - local file path -> read from disk
   - raw base64 -> used as-is
   - empty / omitted -> read from the SYSTEM CLIPBOARD

   Decide what to pass based on what the user gave you: if they gave a URL /
   file path / base64, pass it; if they said "my screenshot" without giving an
   address, leave `image` empty so the tool reads the clipboard.

3. If the message contains an image placeholder like `[Image 1]`, `[Image N]`,
   `[图片]`, or `[Image attachment]` (this happens when the user pasted an
   image attachment but the client/gateway replaced the real image data with
   a placeholder because the main model has no vision), call `describe_image`
   with `image` empty - the image still lives in the OS clipboard. Do this
   even if the user sent no text at all. After getting the description, tell
   the user what you saw and ask what they need.

4. The tool returns a text description of the image, NOT the final answer.
   After the tool returns, YOU (the main model) reason over the description
   and answer the user yourself.

## Where each client reads its rules

Copy the rules above into the file matching your client:

| Client | Global rules file | Project rules file |
|---|---|---|
| opencode | `~/.config/opencode/AGENTS.md` | `./AGENTS.md` |
| Claude Desktop / Claude Code | n/a (use project) | `./CLAUDE.md` |
| Cursor | `~/.cursor/rules/*.mdc` or global settings | `./.cursorrules` or `./.cursor/rules/*.mdc` |
| Windsurf | n/a | `./.windsurfrules` |
| Cline / Roo | n/a | `./.clinerules` |
| GitHub Copilot | n/a | `./.github/copilot-instructions.md` |
| Continue | n/a | `./.continue/config.json` (system prompt field) |
| Aider | n/a | `./CONVENTIONS.md` or `./.aider.conf.yml` |
| Generic | n/a | `./CONTEXT.md` / `./RULES.md` (manual reference) |

For global rules that apply to ALL your sessions of a client, use the global
path. For project-specific rules, use the project path.

## One-time setup per client

1. Make sure the `multimodal` MCP server is registered in the client's config
   (see README.md for the per-client config snippet).
2. Copy rules 1-4 above into the client's rules file (global or project).
3. Restart the client so it picks up both the MCP server and the rules.
