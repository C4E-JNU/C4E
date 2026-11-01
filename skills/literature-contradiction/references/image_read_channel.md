# Pixel Reading Channel (image_read) — shipped implementation and usage

> **Status: applied to the code and passed end-to-end self-checks** (`backend/tools/file_tools.py` / `backend/agent/agent.py` / `backend/agent/subagent.py`).
> The changes are **additive**: behaviour without images is exactly as before. **The backend must be restarted** for them to take effect.

## 1. How the channel works (principle)

Tools can emit an image in **three forms** (engine-level switch `file_tools.set_image_emit(mode)`):

| Form | Tool return | Who turns it into an image the model can see | Purpose |
|---|---|---|---|
| `blocks` (default) | `[{"type":"text",...},{"type":"image_url","image_url":{"url":"[image omitted]"}}]` | **langchain engine**: LangChain/LangGraph natively consume `list[dict]` blocks — `langchain_core.tools.base._format_output` → `_normalize_message_content` puts them into `ToolMessage.content` (the type must be in `TOOL_MESSAGE_BLOCK_TYPES`), and `langchain_openai._sanitize_chat_completions_content` sends them on the wire unchanged. **custom engine**: the loop promotes the image into a separate `user` image block. | Main path |
| `hoist` | JSON text containing a `data_url` | The engine sniffs and hoists it (legacy behaviour) | Fallback / diagnostics |
| `off` | A hint only, no base64 | — | When the endpoint/model has no vision support |

**Why base64 must never stay in the tool message**: OpenAI-family `role=tool` accepts strings only, so a dataURL stuffed in as text is encoded into tokens (hundreds of thousands of characters of noise) and the model **never sees the image**. The text block in `blocks` form therefore contains no data_url, and `hoist` strips `data_url` from its meta too.

| Stage | Implementation |
|---|---|
| Fetch | `read_file(path="x.jpg")` or `read_file(file_id=...)` |
| Size gate | `IMAGE_INLINE_MAX_CHARS = 1600000` (≈1.2 MB original image as dataURL); beyond it returns `mode=image_too_large` |
| Split helper | `split_image_result(result) -> (data_url, text)`, shared by both engine loops and the subagent; `extract_image_data_url()` is a thin wrapper |
| Subagent | `subagent.py` handles it the same way and **extracts the image before the 4,000-character truncation**; but it **cannot crop** (no write permission), so panel cropping is the orchestrator's job |
| Engine switch | custom streaming = `blocks`; langchain = `blocks` (set `C4EAI_LC_IMAGE_MODE=off` to disable); blocking entry point = `off` |

## 2. Self-check record (measured 2026-09, all passed)

```
[1] blocks: returns a list, block types ['text','image_url'], both inside the framework whitelist
[2] hoist : returns JSON text, meta has data_url stripped (no base64 left in the tool message)
[3] off   : only a hint, no base64 returned; an oversized image gets a crop hint
[4] BaseTool.arun(tool_call_id=…)  → ToolMessage.content is content blocks (not stringified)
[5] real create_agent loop (fake model recording messages): in the second round the model's ToolMessage
    content = ['text','image_url'], image_url = [image omitted]… (no base64 text residue)
[6] langchain_openai._convert_message_to_dict → {role: tool, content: [text, image_url]}  ← the dict actually sent
[7] custom entry tool.ainvoke(args) returns a list; after hoisting the stub text has no base64 and the image enters the user message
[8] off form degrades to a text hint in the same loop, with no base64
```

## 3. Usage (do this before the figure ledger; the orchestrator runs it)

```python
# ① crop the target figure into panels (run_code's working directory is the output area; write the filename directly)
from PIL import Image
im = Image.open(r"<archive>/images/Figure6/<hash>.jpg")
im.crop((0, 0, im.width, im.height//2)).convert("L").save("fig6_top.png", optimize=True)
```
② `list_outputs()` → get the `file_id` → ③ `read_file(file_id="...")` → the model sees the image → ④ transcribe panel by panel into a "pixel reading table" (`panel / axis range / legend / reading / unit / uncertainty`).

**Size gate**: a single dataURL ≤ `IMAGE_INLINE_MAX_CHARS = 1600000` characters (measured 2026-10-05; the old value of 200000 is deprecated); beyond it the tool returns `mode=image_too_large` with handling advice. Over the limit, **crop panels / downscale / split in two and register each half**.
⚠️ **Never lower JPEG quality to "save tokens"**: visual tokens scale only with pixel count (about 1 token per 1,024 px), so lowering quality loses information for free and saves nothing. To save, reduce pixel dimensions.
**Registration gate**: reading by `file_id` requires the image to be in the output area (`users/<u>/outputs/`) and registered — `list_outputs()` registers it automatically.
**Path read**: `read_file(path="...")` also works (absolute paths are allowed in non-secure mode), skipping the registration step.

## 4. Constants and rollback

| Item | Location | Note |
|---|---|---|
| `IMAGE_INLINE_MAX_CHARS = 1600000` | top of `file_tools.py` | Per-image dataURL limit; overridable with `C4EAI_IMAGE_INLINE_MAX_CHARS` |
| `set_image_emit(mode)` | `file_tools.py` | Three-state channel switch: `blocks`/`hoist`/`off` |
| `split_image_result(result)` | `file_tools.py` | Split helper (returns `data_url, text`), shared by both engine loops |
| `C4EAI_LC_IMAGE_MODE` | environment variable | langchain engine image channel, `blocks` by default; set `off` and restart on an endpoint 400 |
| Rollback | `git checkout backend/tools/file_tools.py backend/agent/agent.py backend/agent/subagent.py` | Three additive changes, revertible as a whole |

## 5. Discipline (without touching code, change behaviour)

- A reading must carry an **uncertainty** (half the smallest axis tick); a reading without one never enters the ledger.
- At most 3 images per step (already enforced in code); never stuff ten in at once — the context will overflow.
- If it cannot be read, mark "needs figure check"; **inventing numbers is forbidden** (historical incident: an unread source produced 0.88 → a false contradiction).
- The model must support vision; with a text-only model the image becomes an invalid payload — in that case turn the channel off (custom `off`, langchain `C4EAI_LC_IMAGE_MODE=off`) and fall back to "the user sends the image as an attachment for that turn".
- If the endpoint **rejects images inside `role=tool`** (a 400), fall the langchain engine back to `off`; custom is unaffected (it hoists into a `user` message).
- Back up files before changing code; afterwards **restart the backend** (`python backend/app.py`) for the change to take effect.
