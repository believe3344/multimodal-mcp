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

3. Image placeholder resolution order:
   - If the message contains a `[Multimodal attachment paths: ...]` marker, use
     those local paths in the listed order. Pass them to `describe_image` for a
     single image or `describe_images` for multiple images.
   - If the message contains placeholders like `[Image 1]`, `[Image N]`,
     `[图片]`, or `[Image attachment]` but no path marker, count the
     placeholders and call `describe_pasted_images(count=N)`. This reads the
     most recent attachments from OpenCode's attachment cache and restores
     their original paste order.
   - If `describe_pasted_images` fails (for example, the attachment cache is
     empty or has fewer than `N` images), fall back to `describe_image` with
     `image` empty to read the system clipboard.
   - Do this even if the user sent no text at all. After getting the
     description, tell the user what you saw and ask what they need.

4. The tool returns a text description of the image, NOT the final answer.
   After the tool returns, YOU (the main model) reason over the description
   and answer the user yourself.

5. When the user provides multiple images at once, wants to compare images,
   or needs to read multiple screenshots in succession, call `describe_images`
   and keep the user's image order.

6. When the user provides a PDF, call `describe_pdf`. Digital PDF pages are
   extracted as text directly; scanned pages are visually recognized. Use
   `pages="1-3,5"` to specify page ranges.

7. `describe_image`, `describe_images`, and scanned PDF pages return an
   `image_id`. When the user asks a follow-up question about a recently
   described image, prefer `ask_image(image_id, question)` — do not ask
   the user to upload the image again.

8. `image_id` is only valid within the current MCP process (30 minute TTL).
    If expired, re-call the original description tool. Never treat `image_id`
    as a permanent file identifier.

9. If a describe tool returns `status: processing` and a `job_id`, do not
   call the describe tool again. Call `get_recognition(job_id, wait_seconds=50)`
   exactly once. If it is still processing, tell the user the task is still
   running; do not make further status calls in the current turn.

10. When a PDF task returns partial results, answer from completed pages and
    clearly report failed page numbers. Retry only failed pages when the user
    asks.

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
2. Copy rules 1-5 above into the client's rules file (global or project).
3. Restart the client so it picks up both the MCP server and the rules.
