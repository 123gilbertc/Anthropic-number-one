import React from "react";
import {
  AbsoluteFill,
  Sequence,
  useCurrentFrame,
  interpolate,
  Img,
  staticFile,
} from "remotion";

const PHOTOS = [
  staticFile("photos/photo1.jpg"),
  staticFile("photos/photo2.jpg"),
  staticFile("photos/photo3.jpg"),
  staticFile("photos/photo4.jpg"),
  staticFile("photos/photo5.jpg"),
  staticFile("photos/photo6.jpg"),
  staticFile("photos/photo7.jpg"),
];

const CLIP_DURATION = 45; // 1.5s each @ 30fps
export const ROOF_TOTAL_FRAMES = CLIP_DURATION * PHOTOS.length; // 315 frames = 10.5s

// Ken Burns directions per clip for variety
const KB_EFFECTS: Array<{ startScale: number; endScale: number; startX: number; endX: number; startY: number; endY: number }> = [
  { startScale: 1.08, endScale: 1.18, startX: 0,    endX: -2,   startY: 0,   endY: -1 },
  { startScale: 1.15, endScale: 1.05, startX: -2,   endX: 2,    startY: -1,  endY: 1  },
  { startScale: 1.05, endScale: 1.15, startX: 1,    endX: -1,   startY: 0,   endY: -2 },
  { startScale: 1.12, endScale: 1.2,  startX: 0,    endX: 2,    startY: -2,  endY: 0  },
  { startScale: 1.18, endScale: 1.08, startX: 2,    endX: -2,   startY: 0,   endY: 2  },
  { startScale: 1.06, endScale: 1.16, startX: -1,   endX: 1,    startY: 2,   endY: -1 },
  { startScale: 1.1,  endScale: 1.2,  startX: 0,    endX: 0,    startY: -2,  endY: 2  },
];

const PhotoClip: React.FC<{ index: number }> = ({ index }) => {
  const frame = useCurrentFrame();
  const kb = KB_EFFECTS[index];

  const t = frame / CLIP_DURATION;

  const scale = interpolate(t, [0, 1], [kb.startScale, kb.endScale]);
  const tx = interpolate(t, [0, 1], [kb.startX, kb.endX]);
  const ty = interpolate(t, [0, 1], [kb.startY, kb.endY]);

  // Flash cut — brief white flash at start of each clip
  const flashOpacity = interpolate(frame, [0, 4], [0.6, 0], {
    extrapolateRight: "clamp",
  });

  // Fade in
  const opacity = interpolate(frame, [0, 8], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ background: "#000", overflow: "hidden" }}>
      {/* Photo with Ken Burns */}
      <AbsoluteFill
        style={{
          opacity,
          transform: `scale(${scale}) translate(${tx}%, ${ty}%)`,
        }}
      >
        <Img
          src={PHOTOS[index]}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
          }}
        />
      </AbsoluteFill>

      {/* Cinematic letterbox bars */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: 80,
          background: "#000",
          zIndex: 10,
        }}
      />
      <div
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          right: 0,
          height: 80,
          background: "#000",
          zIndex: 10,
        }}
      />

      {/* Step counter */}
      <div
        style={{
          position: "absolute",
          bottom: 100,
          right: 48,
          zIndex: 20,
          color: "white",
          fontFamily: "'Arial Black', Arial, sans-serif",
          fontSize: 36,
          fontWeight: 900,
          letterSpacing: 2,
          opacity: interpolate(frame, [8, 16], [0, 1], { extrapolateRight: "clamp" }),
          textShadow: "0 2px 12px rgba(0,0,0,0.8)",
        }}
      >
        {index + 1} / {PHOTOS.length}
      </div>

      {/* Progress bar */}
      <div
        style={{
          position: "absolute",
          bottom: 80,
          left: 0,
          right: 0,
          height: 4,
          background: "rgba(255,255,255,0.15)",
          zIndex: 20,
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${((index + t) / PHOTOS.length) * 100}%`,
            background: "#FF6600",
            boxShadow: "0 0 8px #FF6600",
          }}
        />
      </div>

      {/* Flash cut overlay */}
      <AbsoluteFill
        style={{
          background: "white",
          opacity: flashOpacity,
          zIndex: 30,
          pointerEvents: "none",
        }}
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
      <div
        style={{
          opacity,
          transform: `scale(${scale})`,
          textAlign: "center",
        }}
      >
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
          Roof Replacement · {PHOTOS.length} Steps
        </div>
      </div>

      {/* Orange line */}
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

export const RoofVideo: React.FC = () => {
  const OUTRO_DURATION = 60;
  return (
    <AbsoluteFill style={{ background: "#000" }}>
      {PHOTOS.map((_, i) => (
        <Sequence
          key={i}
          from={i * CLIP_DURATION}
          durationInFrames={CLIP_DURATION}
        >
          <PhotoClip index={i} />
        </Sequence>
      ))}
      <Sequence from={ROOF_TOTAL_FRAMES} durationInFrames={OUTRO_DURATION}>
        <Outro />
      </Sequence>
    </AbsoluteFill>
  );
};

export const ROOF_COMPOSITION_DURATION = ROOF_TOTAL_FRAMES + 60;
