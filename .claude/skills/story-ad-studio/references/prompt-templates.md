# Prompt templates

Fill in the `{braces}`. Paste locked blocks verbatim. Never paraphrase them between shots.

## Style Lock (write once per project)

```
Cinematic film still, anamorphic 2.35:1 composition, shot on a full-frame cinema camera with a 40mm anamorphic lens at f/2. Dark, moody grade: deep crushed blacks, {palette, e.g. teal shadows and warm amber practicals}, soft single-source {key light direction} light, gentle haze, subtle film grain, shallow depth of field. Subject in the centre band of the frame. No text, no logos, no watermarks.
```

## Character / Product Block (write once per project)

```
{Name}: {age} {gender presentation}, {build}, {hair: colour, length, style}, {face: 2-3 distinctive features}, wearing {exact outfit with colours and materials}, {one signature detail}.
```
```
{Product}: {exact form}, {material and finish}, {exact colours}, {label/branding placement}, resting on {surface}, lit {lighting phrase}.
```

## Keyframe prompt (`generate-image`, `aspect_ratio: "2.35:1"`)

```
{Character/Product Block}. {Action in one clause}. {Shot size} at {angle}, {composition note}. {Location and time of day}. {Style Lock}
```
For shots after the hero, add the approved hero in `reference_image_urls` and start with: `Same person and lighting as image 1.`

**Example:**
```
Maya: early 30s woman, slim, shoulder-length dark curly hair, freckles and a small scar above her left eyebrow, wearing a charcoal wool coat over a cream turtleneck, thin gold ring on her right hand. She sits alone at a kitchen table at night, staring at an unopened letter. Medium close-up at eye level, subject left of centre, empty chair on the right. Small apartment kitchen, 2am. Cinematic film still, anamorphic 2.35:1 composition, ... (Style Lock)
```

## Motion prompt (`generate-video`, `first_frame_image_url` = keyframe)

```
Starting from the provided frame. {What moves: the subject's action, plus one environmental motion}. {ONE camera move: start → path → end → speed}. {Atmosphere: haze drifting, dust in the light, steam}. Calm, cinematic pacing.
```

**Example:**
```
Starting from the provided frame. She slowly reaches for the letter and stops, her hand hovering above it. Steam drifts up from the mug beside her. Slow dolly push from medium close-up to tight close-up on her face over 5 seconds, steady and smooth. Faint haze in the lamp light. Calm, cinematic pacing.
```

**Locked-off:**
```
Starting from the provided frame. Only her eyes move, glancing toward the door. Locked-off camera; the camera remains completely still. Calm, cinematic pacing.
```

## Shot list row

| # | VO phrase | Size / angle | Camera move | Model | Gen s | Trim s | Emphasis words |
|---|---|---|---|---|---|---|---|
| 01 | "Every night, the same question." | MCU, eye level | Slow push-in | kling-v3.0-pro | 5 | 3.2 | **same question** |

## ElevenLabs VO brief

```
Voice: {voice name/ID}, {calm, warm, low register, unhurried}
Settings: Stability 0.48 · Similarity 0.75 · Style 0 · Speed 0.95
Format: WAV, one file per beat: vo_01_hook ... vo_06_cta
Direction: commas = short pause, ellipsis = long pause. Leave a 0.8-1.2 s pause after the hook line and before the payoff line.
```

## Suno music brief

```
Style: {genre, e.g. cinematic ambient / minimal piano and strings / dark electronic pulse}, instrumental, {BPM, e.g. 70-80} BPM, {key mood: melancholic to hopeful}
Arc: soft sparse intro (0-{hook end}s) -> restrained bed under the story -> gentle lift into the payoff at ~{payoff time}s -> resolve and hold on the CTA
Avoid: vocals, busy drums, big drops, anything that competes with a spoken voiceover
Length: at least {ad length + 10}s
```
Pick the take whose lift lands nearest the payoff time. Trim music to the cut, never the cut to the music, because the VO leads.

## Caption sheet (from `transcribe-audio`, `timestamps: "word"`)

Output JSON or a table: `word, start, end, emphasis (true/false), mode (replace | karaoke)`. Mark emphasis words from the script's bold. Hook and payoff lines use `replace`; body lines use `karaoke`.
