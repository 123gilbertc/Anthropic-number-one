import React from "react";
import {
  AbsoluteFill,
  Sequence,
  useCurrentFrame,
  interpolate,
  staticFile,
  OffthreadVideo,
} from "remotion";

const CLIP_COUNT = 7;
const CLIPS = Array.from({ length: CLIP_COUNT }, (_, i) =>
  staticFile(`clips/clip${i + 1}.mp4`)
);

const CLIP_DURATION = 150; // 5s each @ 30fps (matches Kling 5s output)
export const ROOF_TOTAL_FRAMES = CLIP_DURATION * CLIP_COUNT; // 1050 frames = 35s

const TRANSITION_FRAMES = 8; // quick flash cut overlap

const ClipScene: React.FC<{ index: number }> = ({ index }) => {
  const frame = useCurrentFrame();

  // Flash cut at start
  const flashOpacity = interpolate(frame, [0, 5], [0.7, 0], {
    extrapolateRight: "clamp",
  });

  // Fade in
  const opacity = interpolate(frame, [0, 10], [0, 1], {
    extrapolateRight: "clamp",
  });

  // Fade out near end
  const fadeOut = interpolate(
    frame,
    [CLIP_DURATION - TRANSITION_FRAMES, CLIP_DURATION],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  const t = frame / CLIP_DURATION;

  return (
    <AbsoluteFill style={{ background: "#000", overflow: "hidden" }}>
      {/* AI-generated clip */}
      <AbsoluteFill style={{ opacity: Math.min(opacity, fadeOut) }}>
        <OffthreadVideo
          src={CLIPS[index]}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      </AbsoluteFill>

      {/* Cinematic letterbox */}
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 72, background: "#000", zIndex: 10 }} />
      <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 72, background: "#000", zIndex: 10 }} />

      {/* Step counter */}
      <div
        style={{
          position: "absolute",
          bottom: 90,
          right: 48,
          zIndex: 20,
          color: "white",
          fontFamily: "'Arial Black', Arial, sans-serif",
          fontSize: 34,
          fontWeight: 900,
          letterSpacing: 2,
          opacity: interpolate(frame, [10, 20], [0, 1], { extrapolateRight: "clamp" }),
          textShadow: "0 2px 16px rgba(0,0,0,0.9)",
        }}
      >
        {index + 1} / {CLIP_COUNT}
      </div>

      {/* Progress bar */}
      <div
        style={{
          position: "absolute", bottom: 72, left: 0, right: 0,
          height: 4, background: "rgba(255,255,255,0.15)", zIndex: 20,
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${((index + t) / CLIP_COUNT) * 100}%`,
            background: "#FF6600",
            boxShadow: "0 0 8px #FF6600",
          }}
        />
      </div>

      {/* Flash cut overlay */}
      <AbsoluteFill
        style={{ background: "white", opacity: flashOpacity, zIndex: 30, pointerEvents: "none" }}
      />
    </AbsoluteFill>
  );
};

const Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: "clamp" });
  const scale = interpolate(frame, [0, 20], [0.9, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill
      style={{
        background: "#0a0a0a",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
        gap: 16,
      }}
    >
      <div style={{ opacity, transform: `scale(${scale})`, textAlign: "center" }}>
        <div
          style={{
            color: "#FF6600",
            fontFamily: "'Arial Black', Arial, sans-serif",
            fontSize: 64,
            fontWeight: 900,
            letterSpacing: 4,
            textTransform: "uppercase",
            textShadow: "0 0 30px #FF6600aa",
          }}
        >
          JOB COMPLETE
        </div>
        <div
          style={{
            color: "rgba(255,255,255,0.6)",
            fontFamily: "Arial, sans-serif",
            fontSize: 28,
            letterSpacing: 6,
            marginTop: 12,
            textTransform: "uppercase",
          }}
        >
          Roof Replacement · {CLIP_COUNT} Steps
        </div>
      </div>
      <div
        style={{
          width: interpolate(frame, [20, 40], [0, 300], { extrapolateRight: "clamp" }),
          height: 3,
          background: "#FF6600",
          marginTop: 24,
          boxShadow: "0 0 12px #FF6600",
        }}
      />
    </AbsoluteFill>
  );
};

const OUTRO_DURATION = 60;
export const ROOF_COMPOSITION_DURATION = ROOF_TOTAL_FRAMES + OUTRO_DURATION;

export const RoofVideo: React.FC = () => (
  <AbsoluteFill style={{ background: "#000" }}>
    {CLIPS.map((_, i) => (
      <Sequence key={i} from={i * CLIP_DURATION} durationInFrames={CLIP_DURATION + TRANSITION_FRAMES}>
        <ClipScene index={i} />
      </Sequence>
    ))}
    <Sequence from={ROOF_TOTAL_FRAMES} durationInFrames={OUTRO_DURATION}>
      <Outro />
    </Sequence>
  </AbsoluteFill>
);
