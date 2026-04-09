import React from "react";
import { AbsoluteFill, useCurrentFrame, Sequence } from "remotion";
import { Scene1, Scene2, Scene3, Scene4 } from "./adScenes1";
import { Scene5, Scene6, Scene7, Scene8 } from "./adScenes2";
import { Scene9, Scene10, Scene11, Scene12 } from "./adScenes3";

// ─── Scene timing (30 fps) ────────────────────────────────────────
//  S1:  0– 90  (0–3s)   Title card
//  S2:  90–180  (3–6s)   ROAS drop
//  S3: 180–270  (6–9s)   Budget doubled
//  S4: 270–360  (9–12s)  Executives
//  S5: 360–420  (12–14s) Nobody told you the truth
//  S6: 420–540  (14–18s) Creative ceiling
//  S7: 540–690  (18–23s) Audience fatigue
//  S8: 690–810  (23–27s) 3 new ads
//  S9: 810–900  (27–30s) Done for you
// S10: 900–1020 (30–34s) Revenue counter
// S11:1020–1140 (34–38s) DM SCALE
// S12:1140–1260 (38–42s) End card
// ─────────────────────────────────────────────────────────────────

const SCENES: { start: number; duration: number; component: React.FC<{ localFrame: number }> }[] = [
  { start: 0,    duration: 90,  component: Scene1 },
  { start: 90,   duration: 90,  component: Scene2 },
  { start: 180,  duration: 90,  component: Scene3 },
  { start: 270,  duration: 90,  component: Scene4 },
  { start: 360,  duration: 60,  component: Scene5 },
  { start: 420,  duration: 120, component: Scene6 },
  { start: 540,  duration: 150, component: Scene7 },
  { start: 690,  duration: 120, component: Scene8 },
  { start: 810,  duration: 90,  component: Scene9 },
  { start: 900,  duration: 120, component: Scene10 },
  { start: 1020, duration: 120, component: Scene11 },
  { start: 1140, duration: 120, component: Scene12 },
];

export const TOTAL_DURATION = 1260; // 42 seconds @ 30fps

export const AdVideo: React.FC = () => {
  const frame = useCurrentFrame();

  return (
    <AbsoluteFill style={{ background: "#000", overflow: "hidden" }}>
      {SCENES.map(({ start, duration, component: SceneComp }, i) => (
        <Sequence key={i} from={start} durationInFrames={duration} layout="none">
          <AbsoluteFill>
            <SceneComp localFrame={frame - start} />
          </AbsoluteFill>
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
