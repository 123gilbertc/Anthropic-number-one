# Camera moves in prose that OpenRouter video models follow

OpenRouter models (Kling, Veo, Seedance, Runway, Wan) take the camera from the prompt text. There are no preset chips. Write every move as **start framing → path → end framing → speed**.

## Rules

1. **One primary move per shot.** Stacked moves cause jitter, unwanted rotation and failed takes.
   - Wrong: "dolly push forward while panning left and tilting up"
   - Right: "slow dolly push from medium shot to tight close-up over 5 seconds"
2. **Always give an end frame and a speed.** Without an end frame the move drifts. Without a speed it whips.
3. **Dolly is not zoom.** A dolly moves through space and shows parallax; a zoom flattens. For a dolly zoom, spell out both halves: "the subject stays the same size while the background stretches away behind them."
4. **Write stillness positively:** "Locked-off camera. The camera remains completely still." Never "no camera movement."
5. **Image-to-video = motion only.** Don't re-describe the subject or the lighting.
6. **For a precise push-in, use start and end frames** (`first_frame_image_url` + `last_frame_image_url`, on models that support both) rather than hoping the prompt lands it.

## The calm story-ad core (use these 80% of the time)

| Move | Prose to paste | Feels like | Best beat |
|---|---|---|---|
| Slow push-in | "slow dolly push from medium shot to tight close-up over 5 seconds, steady and smooth" | Intimacy, realisation | Problem, Payoff |
| Slow pull-out | "slow dolly pull back from close-up to wide, revealing the empty room around her, over 5 seconds" | Isolation, context | Problem, ending |
| Lateral track | "smooth lateral dolly from left to right at walking pace, parallax in the foreground" | Observation, passage of time | Context |
| Slow arc | "smooth 90-degree arc around the subject at eye level, constant distance, slow" | Elevation, the product as hero | Product |
| Turntable | "the camera orbits the product slowly, a quarter turn over 5 seconds, on a dark surface" | Product hero | Product, Proof |
| Crane up | "camera rises slowly from eye level to a high angle, revealing the city beyond" | Scale, hope | Payoff |
| Crane down | "camera descends slowly from a high wide view down to her at the desk" | Arrival, focus | Hook, Context |
| Locked-off | "locked-off camera, the camera remains completely still; only the steam rises" | Tension, a held breath | Before payoff, CTA |

## Energy accents (one or two per ad, max)

| Move | Prose | Use |
|---|---|---|
| Fast rush-in | "fast push-in from wide to close-up in the first second, then settle" | Hook hit |
| Crash zoom | "sudden crash zoom into her eyes" | Shock beat |
| Whip pan | "fast whip pan to the right with motion blur" | Generated transition between scenes |
| Through-object | "the camera glides forward through the gap in the door into the bright room beyond" | Generated scene transition |

Avoid for this style: handheld shake, FPV drone, bullet time, snorricam. They're unreliable as pure generation and off-tone for calm ads.

## Framing (not movement; say it in the keyframe prompt)

Sizes: Extreme wide → Wide → Medium wide → Medium → Medium close-up → Close-up → Extreme close-up → Insert.
Angles: Low angle (power), High angle (vulnerability), Eye level, Overhead, Dutch tilt (unease), Over-the-shoulder, POV.

## By genre

- **Product / e-commerce:** turntable, slow arc, slow push-in, locked-off. Avoid handheld and whip pans.
- **Drama / narrative:** slow push-in, pull-out, lateral track, crane up. Avoid fast moves.
- **Tension / problem beats:** slow creeping push-in, locked-off hold, slight Dutch angle in the keyframe.
- **Landscape / payoff:** crane up, slow pan, slow aerial drift forward.

## Compatible pairs (only if a shot truly needs two)
Push-in + Dutch angle (the angle is in the keyframe) · Crane up + slow arc · Fast rush-in + settle. Never put opposite moves in one shot (push-in + pull-out, crane up + crane down).

## Model notes
- **Kling:** order the prompt subject → subject motion → scene → camera, light and atmosphere last. Specific beats vague: "camera slowly pushes in on her face as she turns toward the window."
- **Runway Gen-4.5:** layer one thing at a time: subject motion → camera → scene motion → style. Sequenced prose works ("hold, then push in, then settle").
- **Veo 3.1:** name the camera mount or rig for vehicle and motion shots. Keep `generate_audio` off unless the shot needs in-scene sound.
- **Seedance:** strong with reference images; keep the motion prompt short.
