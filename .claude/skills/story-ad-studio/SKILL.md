---
name: story-ad-studio
description: Builds story-led cinematic video ads (calm voiceover driving hard cuts, word-by-word kinetic type, slow camera moves, letterboxed ~2.35:1 picture in a 16:9 file, dark cinematic grade) on Gil's stack - OpenRouter for keyframes and video clips, ElevenLabs for voiceover, Suno for music. Use whenever the user asks to make, plan, script, storyboard, prompt, budget or assemble a story-led / cinematic / Higgsfield-style ad, or asks which OpenRouter model, camera move or setting to use for a shot.
---

# Story Ad Studio

One brief in, a cut-ready asset set out: VO stems, Suno music brief, letterboxed keyframes, animated clips, a word-timed caption sheet, and an edit plan. Assembly (cuts, type, grade, mix) still happens in the editor. No tool on any route returns a finished ad.

**The stack, and only this stack:**

| Job | Tool | How |
|---|---|---|
| Keyframes (stills) | OpenRouter | `generate-image` MCP tool |
| Video clips | OpenRouter | `generate-video`, then poll `get-video` |
| Word timings for type | OpenRouter | `transcribe-audio` with `timestamps: "word"` on the final VO |
| Voiceover | ElevenLabs | User's ElevenLabs account, one file per beat |
| Music | Suno | User generates from the brief this skill writes |
| Model and price lookups | OpenRouter | `list-models`, `get-model` (free) |

Don't add Higgsfield, fal, Replicate or open-source routes. Higgsfield preset names (DoP, Soul ID, Popcorn chips) do nothing on OpenRouter. Write camera moves as plain prose (see `references/camera-moves.md`).

## Non-negotiables (these prevent most failures and wasted money)

1. **Budget gate before every billed batch.** Before any `generate-video` or batch of `generate-image` calls, show: model, seconds × count, estimated $, running total. Wait for an OK. Every completed generation is billed, usable or not. Video prices aren't in `list-models` (it shows $0), so use `references/models-and-costs.md` and say they need re-checking on the model's openrouter.ai page.
2. **Check the model before using a feature.** Run `get-model` before sending `first_frame_image_url`, `last_frame_image_url`, `reference_image_urls`, a `duration` or `generate_audio`. The API rejects unsupported inputs, and support varies by model.
3. **Fix the still, never the video.** Approve keyframes before animating. A still costs cents to redo; a clip costs dollars.
4. **One phrase = one shot = one camera move.** Never stack moves.
5. **Image-to-video prompts describe motion only.** The keyframe already sets the subject, light and colour. Describing them again causes drift.
6. **Draft cheap, finish premium.** Iterate at 720p, silent, on a draft-tier model. Re-run only the approved shots on the hero model. Use native audio only for a shot that truly needs in-shot sound.
7. **Download outputs right away.** `get-video` links can expire within about 24 h. Unsigned links need the OpenRouter key as a Bearer header. Save every keeper to the project's `assets/` folder with its shot number.
8. **Poll patiently.** Jobs take 1–5 min. Call `get-video` every 15–20 s until `completed` / `failed` / `expired`. Don't resubmit a job that is still running, or you pay twice.

## Pipeline

Work through these in order and get the user's sign-off at each ✋.

### 1. Brief ✋
Pin down: one message, one audience, one payoff line, who or what carries the story (product, person or object), length (:30 / :45 / :60), CTA and end card. Delivery is always a 16:9 file with a ~2.35:1 picture.

### 2. VO script, word-budgeted ✋
- Beats: **Hook (0–3 s) → Problem/Context → Product → Proof → Payoff → CTA**. A beat is one thing the viewer learns or feels.
- Word budget at a calm ~130 wpm: **:30 ≈ 55–65 words, :45 ≈ 75–95, :60 ≈ 115–130.** Keep a beat of silence after the hook and before the payoff. Those pauses carry the style.
- Punctuation is direction: commas and ellipses become pauses in ElevenLabs.
- Output a two-column AV table (Video | Audio/SUPER), one row per phrase, with its 2–4 emphasis words marked in **bold**.
- Read-aloud check: estimate each phrase's length (words ÷ 2.2 ≈ seconds). Cut adjectives first, then whole lines.

### 3. Shot list ✋
One row per VO phrase: shot #, size, angle, ONE camera move (from `references/camera-moves.md`), clip length to generate (5 s default) and trimmed length (2.5–4 s). A :30 spot runs 8–12 shots, a :45 runs 12–16, a :60 runs 15–24. Change the picture every 2.5–4 s.

### 4. Lock the look and identity (once, before any generation)
Write one **Style Lock** and one **Character/Product Block** (templates in `references/prompt-templates.md`). Paste them verbatim into every keyframe prompt and never paraphrase them. Changing style mid-project means regenerating every earlier shot.

### 5. Hero frame, then keyframes ✋
- Generate shot 1 first with `generate-image`, `aspect_ratio: "2.35:1"`. Approve it before generating the rest, because it sets tone, light and palette for the whole film.
- For the remaining keyframes, pass the approved hero (and any product or character reference) in `reference_image_urls` and refer to it in the prompt as "image 1". That holds identity far better than text alone.
- Prompt order: Subject (first 20–30 words) → Action → Camera framing → Look. Keep it under ~200 words.
- Check continuity: does the end of shot N match the start of shot N+1? Fix it here.
- For a controlled push-in or reveal, also generate the **end frame** (same scene, tighter framing).

### 6. Animate ✋ (budget gate)
- Default: `aspect_ratio: "21:9"`, `resolution: "720p"`, `duration` = the clip length the model supports (≈5 s), `generate_audio: false`, `first_frame_image_url` = the approved keyframe. 21:9 (≈2.33:1) fills the letterbox exactly with no crop. Check the model supports 21:9 first; otherwise generate 16:9 with the subject centred and crop to the band in the edit.
- Prompt = what moves + ONE camera move with start → path → end → speed + atmosphere. Templates in `references/prompt-templates.md`.
- Pick the model per shot type from `references/models-and-costs.md`. Pass `seed` so a retake is reproducible where the model honours it.
- Expect 3–5 takes per kept shot. If a shot fails twice, change the keyframe or the move instead of re-rolling the same prompt.

### 7. Voiceover (ElevenLabs) ✋
- One file per beat. Name them `vo_01_hook.wav`, `vo_02_problem.wav` and so on. Use WAV where the plan allows.
- Calm-read starting settings: **Stability 0.45–0.50, Similarity 0.75, Style 0, Speed 0.93–0.98.**
- Lock the VO before the edit. Picture is cut to the voice, never the other way around.
- Then run `transcribe-audio` (`timestamps: "word"`) on each final file (or the joined VO) to get word start/end times for the type. Check slugs with `list-models output_modalities=transcription`.

### 8. Music (Suno)
Write the Suno brief from `references/prompt-templates.md`: instrumental, BPM, mood arc that matches the beats (restrained bed → lift into payoff → resolve on CTA), and a length of at least the ad plus 10 s. The user generates it in Suno. Use the instrumental only. Lyrics fight the VO.

### 9. Edit plan (hand to the editor: Descript, CapCut, Premiere or Resolve)
Output a cut sheet with one row per shot: in/out on the VO timeline, the clip and trim, type cue and emphasis styling, SFX hit. Then the finishing spec:
- **Order:** VO track first → hard-cut clips to phrase boundaries and stressed words → no dissolves (transitions are generated shots, e.g. whip pan, through-object).
- **Kinetic type:** timed from the word timestamps, not guessed. Either one word replaces the last (hook, payoff) or the phrase stays up and the emphasis follows the voice. Use ONE emphasis device for the whole film: serif italic in a contrasting face (the target look), or a scale or colour pop. 2–4 emphasis words per line max. Hard cut-ins or slams, no soft fades. Type sits inside the picture band, never on the bars (title and end cards excepted).
- **Grade** (one adjustment layer over all clips): exposure and white balance first, then desaturate ~10–15%, then crushed blacks OR lifted blacks (pick one), light vignette, film grain.
- **Letterbox:** 16:9 timeline with black bars top and bottom so the picture area is ~2.35:1 (≈ 1920×817 inside 1920×1080, bars ≈ 131 px each).
- **Mix:** VO → continuous ambience bed per scene (no dead silence) → music at about −22 to −26 dB under the VO, ducking a further 6–8 dB under each line and rising into the planned silences → SFX only on cuts and reveals. Master at −14 to −16 LUFS.
- **Export:** 1080p 16:9. If clips were made at 720p, upscale the approved ones (check `list-models output_modalities=video` for an upscale model such as FLUX Video Upscale, behind a budget gate).

### 10. Final QC
First frame and first word land together. Type reads with the sound off. The ad survives muted autoplay. The same face and product appear in every shot. No AI text artefacts in the picture.

## Where things are saved
Put each project in `ads/<slug>/`: `brief.md`, `script.md` (AV table), `shots.md`, `prompts.md` (every prompt sent, with model, seed and job id), `costs.md` (running ledger), `assets/` (keyframes, clips, VO, music, captions JSON).

## References
- `references/models-and-costs.md`: which OpenRouter model for which shot, per-second prices, budget maths for each ad length.
- `references/camera-moves.md`: one-move vocabulary in prose that video models follow, by genre and by beat.
- `references/prompt-templates.md`: Style Lock, Character/Product Block, keyframe, motion, Suno brief and ElevenLabs templates.
