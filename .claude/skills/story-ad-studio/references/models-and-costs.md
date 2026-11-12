# OpenRouter models and costs for story ads

The slugs below were in OpenRouter's live catalogue on 2026-11-12. The catalogue changes often, so confirm with `list-models` (`output_modalities: "video"` or `"image"`) and `get-model` before a project. `list-models` shows video pricing as $0 because per-second prices aren't in that API yet. Check the price on `https://openrouter.ai/<slug>` before the budget gate.

## Video: pick by shot type

| Shot type | Hero model (final) | Draft model (iteration) | Why |
|---|---|---|---|
| Emotional close-up, faces, reactions | `google/veo-3.1` | `google/veo-3.1-fast` or `google/veo-3.1-lite` | Best faces and micro-expression |
| Slow dolly on a quiet interior or product | `kwaivgi/kling-v3.0-pro` | `kwaivgi/kling-v3.0-std` | Clean slow moves; first- and last-frame control |
| Controlled push-in or reveal (start + end frame) | `kwaivgi/kling-v3.0-pro` or `bytedance/seedance-2.0` | `bytedance/seedance-2.0-fast` | First-and-last-frame support |
| Character consistency across many shots | `bytedance/seedance-2.0` / `bytedance/seedance-2.5` | `bytedance/seedance-2.0-fast` / `-mini` | Strong identity hold; multi-reference |
| Cinematic wide, landscape, epic reveal | `runway/gen-4.5` or `google/veo-3.1` | `alibaba/wan-3.0` | Motion quality, scale |
| Volume or B-roll filler | `alibaba/wan-3.0` / `minimax/hailuo-3` | same | Cheapest per second |

Video helpers (budget-gated, only when needed):
- `black-forest-labs/flux-video-upscale`: 1.5–3× upscale of a kept 720p clip.
- `runway/aleph-2`, `black-forest-labs/flux-video-edit`: fix one thing in a kept clip (remove an object, change the sky) instead of regenerating it.

## Images: keyframes

| Need | Model |
|---|---|
| Photoreal hero frames, product recontext, edits holding identity via `reference_image_urls` | `google/gemini-3-pro-image` (Nano Banana Pro) or `google/gemini-nano-banana-2.1` |
| Dense detail, consistent multi-step edits | `openai/gpt-image-2.6-sunburst` |
| Fast, cheap iteration | `openai/gpt-image-2.6-flare`, `bytedance-seed/seedream-5-0-flash`, `qwen/qwen-image-2.1-turbo` |
| Lifelike commercial scenes, precise edits | `bytedance-seed/seedream-5-0-pro` |
| Multi-reference consistency (up to 10 refs) | `black-forest-labs/flux-3-image` |
| Legible text in frame (packaging, signage) | `qwen/qwen-image-3-pro` |

Always set `aspect_ratio: "2.35:1"` for in-film keyframes. Use `"16:9"` only for full-frame title or end cards.

## Per-second video prices (from the 2026-11-12 research; re-check before spending)

| Model | Silent | With native audio |
|---|---|---|
| `google/veo-3.1` | $0.20/s | $0.40/s |
| `kwaivgi/kling-v3.0-pro` | $0.112/s | $0.168/s |
| `runway/gen-4.5` | $0.12/s | n/a |
| `minimax/hailuo-3` | $0.13/s | — |
| `alibaba/wan-3.0` | $0.05–0.20/s by resolution | — |
| `bytedance/seedance-2.x` | token-priced, so check the model page | — |

Price moves with resolution (720p → 1080p costs 1.5–4×) and native audio (+50–100%) more than with the model. Default to silent 720p.

## Budget maths

Formula: **shots × clip seconds × takes per kept shot × $/s**. Plan on 4 takes (3 for a tight brief, 5 for a loose one).

| Ad | Shots × 5 s × 4 takes | Kling 3.0 Pro silent | Veo 3.1 silent | Wan 3.0 (low tier) |
|---|---|---|---|---|
| :30 | 10 → 200 s | ≈ $22 | ≈ $40 | ≈ $10 |
| :45 | 9–12 → 180–240 s | ≈ $20–27 | ≈ $36–48 | ≈ $9–12 |
| :60 | 18 → 360 s | ≈ $40 | ≈ $72 | ≈ $18 |

Smart mix: draft every shot on a draft model, then finish only the 3–4 hero shots (hook, product reveal, payoff) on Veo 3.1 and the rest on Kling 3.0 Pro. Keyframes add ~$0.10–4 per ad. Transcription costs cents. ElevenLabs and Suno are on their own plans.

Keep a running `costs.md` with one line per billed call: model, seconds, job id, cost (from `get-video` or `get-generation`). Report the total at every budget gate.
