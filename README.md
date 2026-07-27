# AngleCast

> **Record like Google Meet. Walk away with multi-cam podcast footage.**  
> One camera. Infinite angles. Perfect backgrounds.

Browser-based cloud meeting + multi-cam podcast studio with client-side virtual backgrounds and a pluggable AI multi-angle pipeline.

## Monorepo layout

```
apps/web                     Next.js App Router — lobby, LiveKit room, session viewer
packages/shared              Product types, quality presets, angle trajectories
packages/virtual-background  MediaPipe selfie segmenter + WebGL compositor (TrackProcessor)
packages/recording           Local multi-track MediaRecorder + progressive upload hooks
packages/angles              Pluggable generateAngles() interface (stub → Stability/Kling/…)
```

## Phase 1 foundation (this PR)

1. **LiveKit room join** with camera, mic, screen share, gallery + large screen pane  
2. **Production-minded virtual backgrounds** — landscape selfie model, temporal mask EMA, edge softening, adaptive quality  
3. **Demo mode** when LiveKit env is missing (full local camera + VB + recording still works)  
4. **Local multi-track recording** with consent modal + REC indicator  
5. **Post-session viewer stub** + **pluggable angle generator** for Phase 2  

## Quick start

```bash
pnpm install
cp .env.example apps/web/.env.local   # add LiveKit keys for multi-party
pnpm dev
```

Open [http://localhost:3000](http://localhost:3000).

### LiveKit credentials

Create a project at [cloud.livekit.io](https://cloud.livekit.io) (or self-host) and set:

```
LIVEKIT_API_KEY=…
LIVEKIT_API_SECRET=…
NEXT_PUBLIC_LIVEKIT_URL=wss://….livekit.cloud
```

Without these, AngleCast runs in **demo mode** (single-user local studio) so you can still validate backgrounds and recording.

## Architecture notes

- Virtual backgrounds run **entirely client-side** (privacy + latency). Only the composited outbound track hits the SFU.
- Recording prefers **local MediaRecorder per track** (Riverside-style) with optional progressive upload — LiveKit Egress is a backup path later.
- `generateAngles()` is abstracted from day one so Phase-2 novel-view providers swap without touching the editor.

## Scripts

| Command        | Description                |
|----------------|----------------------------|
| `pnpm dev`     | Start Next.js web app      |
| `pnpm build`   | Build packages + web       |
| `pnpm typecheck` | Typecheck all packages   |
