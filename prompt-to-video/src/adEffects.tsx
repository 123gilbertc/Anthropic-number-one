import React, { CSSProperties } from "react";
import { useCurrentFrame, interpolate } from "remotion";

// ─── Color palette ────────────────────────────────────────────────
export const RED = "#FF0033";
export const CYAN = "#00FFFF";
export const WHITE = "#FFFFFF";
export const BG = "#030303";
export const GOLD = "#FFD700";
export const CHROME_GRAD =
  "linear-gradient(180deg,#F0F0F0 0%,#C0C0C0 30%,#808080 60%,#C0C0C0 80%,#F0F0F0 100%)";

// ─── Scanlines overlay ────────────────────────────────────────────
export const Scanlines: React.FC<{ opacity?: number }> = ({
  opacity = 0.18,
}) => (
  <div
    style={{
      position: "absolute",
      inset: 0,
      background:
        "repeating-linear-gradient(0deg,rgba(0,0,0,0.55) 0px,rgba(0,0,0,0.55) 1px,transparent 1px,transparent 4px)",
      opacity,
      pointerEvents: "none",
      zIndex: 999,
    }}
  />
);

// ─── Film-grain noise overlay (CSS-only approximation) ───────────
export const Grain: React.FC = () => {
  const frame = useCurrentFrame();
  // Shift background-position every frame to simulate grain movement
  const ox = (frame * 173) % 200;
  const oy = (frame * 97) % 200;
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        opacity: 0.07,
        backgroundImage:
          "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
        backgroundSize: "200px 200px",
        backgroundPosition: `${ox}px ${oy}px`,
        pointerEvents: "none",
        zIndex: 998,
        mixBlendMode: "overlay",
      }}
    />
  );
};

// ─── Chromatic aberration text ────────────────────────────────────
export const ChromaText: React.FC<{
  children: React.ReactNode;
  style?: CSSProperties;
  spread?: number;
}> = ({ children, style = {}, spread = 4 }) => (
  <div
    style={{
      position: "relative",
      ...style,
      textShadow: `
        -${spread}px 0 ${RED},
         ${spread}px 0 ${CYAN},
         0 0 20px rgba(255,255,255,0.4)
      `,
    }}
  >
    {children}
  </div>
);

// ─── Neon glow text ───────────────────────────────────────────────
export const NeonText: React.FC<{
  children: React.ReactNode;
  color?: string;
  style?: CSSProperties;
  intensity?: number;
}> = ({ children, color = CYAN, style = {}, intensity = 1 }) => (
  <div
    style={{
      ...style,
      textShadow: `
        0 0 ${10 * intensity}px ${color},
        0 0 ${30 * intensity}px ${color},
        0 0 ${60 * intensity}px ${color},
        0 0 ${100 * intensity}px ${color}
      `,
      color: WHITE,
    }}
  >
    {children}
  </div>
);

// ─── Glitch slice effect ──────────────────────────────────────────
export const GlitchText: React.FC<{
  children: React.ReactNode;
  style?: CSSProperties;
  active?: boolean;
}> = ({ children, style = {}, active = true }) => {
  const frame = useCurrentFrame();
  const sliceA = active ? ((frame * 37) % 80) + 10 : 50;
  const sliceB = active ? ((frame * 53) % 60) + 30 : 60;
  const offsetA = active ? Math.sin(frame * 0.7) * 8 : 0;
  const offsetB = active ? Math.cos(frame * 1.1) * -6 : 0;

  return (
    <div style={{ position: "relative", ...style }}>
      {/* Red slice */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          color: RED,
          clipPath: `inset(${sliceA}% 0 ${100 - sliceA - 8}% 0)`,
          transform: `translateX(${offsetA}px)`,
          ...style,
        }}
      >
        {children}
      </div>
      {/* Cyan slice */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          color: CYAN,
          clipPath: `inset(${sliceB}% 0 ${100 - sliceB - 5}% 0)`,
          transform: `translateX(${offsetB}px)`,
          ...style,
        }}
      >
        {children}
      </div>
      {/* Main */}
      <div style={{ position: "relative" }}>{children}</div>
    </div>
  );
};

// ─── Screen shake ─────────────────────────────────────────────────
export function useShake(intensity = 1): CSSProperties {
  const frame = useCurrentFrame();
  const x = Math.sin(frame * 17.3) * 4 * intensity;
  const y = Math.cos(frame * 11.7) * 3 * intensity;
  return { transform: `translate(${x}px, ${y}px)` };
}

// ─── Vignette ─────────────────────────────────────────────────────
export const Vignette: React.FC = () => (
  <div
    style={{
      position: "absolute",
      inset: 0,
      background:
        "radial-gradient(ellipse at center, transparent 40%, rgba(0,0,0,0.85) 100%)",
      pointerEvents: "none",
      zIndex: 900,
    }}
  />
);

// ─── Particles (simple circles flying outward) ────────────────────
export const Particles: React.FC<{
  count?: number;
  colors?: string[];
  startFrame?: number;
  duration?: number;
  cx?: number;
  cy?: number;
  radius?: number;
}> = ({
  count = 24,
  colors = [RED, CYAN, WHITE, GOLD],
  startFrame = 0,
  duration = 60,
  cx = 540,
  cy = 960,
  radius = 600,
}) => {
  const frame = useCurrentFrame();
  const t = Math.max(0, Math.min(1, (frame - startFrame) / duration));
  if (t === 0) return null;

  return (
    <>
      {Array.from({ length: count }, (_, i) => {
        const angle = (i / count) * Math.PI * 2 + i * 0.3;
        const speed = 0.5 + (i % 5) * 0.15;
        const r = radius * t * speed;
        const x = cx + Math.cos(angle) * r;
        const y = cy + Math.sin(angle) * r;
        const size = 4 + (i % 6) * 2;
        const color = colors[i % colors.length];
        const opacity = interpolate(t, [0, 0.3, 1], [0, 1, 0]);
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: x - size / 2,
              top: y - size / 2,
              width: size,
              height: size,
              borderRadius: "50%",
              background: color,
              opacity,
              boxShadow: `0 0 ${size * 2}px ${color}`,
              pointerEvents: "none",
            }}
          />
        );
      })}
    </>
  );
};

// ─── Counter animation ────────────────────────────────────────────
export function useCounter(
  from: number,
  to: number,
  startFrame: number,
  endFrame: number,
  frame: number
): string {
  const val = interpolate(frame, [startFrame, endFrame], [from, to], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return Math.round(val).toLocaleString();
}

// ─── Horizontal glitch bars ───────────────────────────────────────
export const GlitchBars: React.FC<{ intensity?: number }> = ({
  intensity = 1,
}) => {
  const frame = useCurrentFrame();
  const bars = [0.15, 0.35, 0.55, 0.72, 0.88];
  return (
    <>
      {bars.map((pos, i) => {
        const visible = Math.sin(frame * (2.3 + i * 0.7) + i) > 0.6;
        if (!visible) return null;
        const offset = Math.sin(frame * 3.1 + i) * 30 * intensity;
        const h = 3 + (i % 3) * 4;
        const color = i % 2 === 0 ? RED : CYAN;
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              top: `${pos * 100}%`,
              left: 0,
              right: 0,
              height: h,
              background: color,
              opacity: 0.35,
              transform: `translateX(${offset}px)`,
              pointerEvents: "none",
              zIndex: 997,
              mixBlendMode: "screen",
            }}
          />
        );
      })}
    </>
  );
};

// ─── SVG line graph (crashing) ────────────────────────────────────
export const CrashGraph: React.FC<{
  width?: number;
  height?: number;
  progress?: number; // 0-1, how far the crash has progressed
  color?: string;
}> = ({ width = 400, height = 200, progress = 1, color = RED }) => {
  // Path: rises then crashes
  const pts: [number, number][] = [
    [0, height * 0.6],
    [width * 0.15, height * 0.4],
    [width * 0.3, height * 0.25],
    [width * 0.45, height * 0.15],
    [width * 0.5, height * 0.2],
    [width * 0.6, height * 0.55],
    [width * 0.7, height * 0.75],
    [width * 0.8, height * 0.88],
    [width * 0.9, height * 0.96],
    [width, height * 0.98],
  ];
  const totalPts = Math.max(2, Math.round(pts.length * progress));
  const visiblePts = pts.slice(0, totalPts);
  const d =
    visiblePts
      .map((p, i) => `${i === 0 ? "M" : "L"}${p[0]},${p[1]}`)
      .join(" ") + ` L${visiblePts[visiblePts.length - 1][0]},${height}`;

  return (
    <svg width={width} height={height} style={{ overflow: "visible" }}>
      {/* Area fill */}
      <path d={d} fill={`${color}22`} />
      {/* Line */}
      <path
        d={visiblePts
          .map((p, i) => `${i === 0 ? "M" : "L"}${p[0]},${p[1]}`)
          .join(" ")}
        fill="none"
        stroke={color}
        strokeWidth={3}
        filter={`drop-shadow(0 0 6px ${color})`}
      />
      {/* Last point dot */}
      {visiblePts.length > 0 && (
        <circle
          cx={visiblePts[visiblePts.length - 1][0]}
          cy={visiblePts[visiblePts.length - 1][1]}
          r={6}
          fill={color}
          filter={`drop-shadow(0 0 8px ${color})`}
        />
      )}
    </svg>
  );
};

// ─── Rising graph ─────────────────────────────────────────────────
export const RiseGraph: React.FC<{
  width?: number;
  height?: number;
  progress?: number;
  color?: string;
}> = ({ width = 400, height = 200, progress = 1, color = CYAN }) => {
  const pts: [number, number][] = [
    [0, height * 0.95],
    [width * 0.1, height * 0.88],
    [width * 0.25, height * 0.75],
    [width * 0.4, height * 0.55],
    [width * 0.55, height * 0.38],
    [width * 0.7, height * 0.22],
    [width * 0.85, height * 0.1],
    [width, height * 0.04],
  ];
  const totalPts = Math.max(2, Math.round(pts.length * progress));
  const visiblePts = pts.slice(0, totalPts);
  const d =
    visiblePts
      .map((p, i) => `${i === 0 ? "M" : "L"}${p[0]},${p[1]}`)
      .join(" ") + ` L${visiblePts[visiblePts.length - 1][0]},${height} L0,${height}`;

  return (
    <svg width={width} height={height} style={{ overflow: "visible" }}>
      <path d={d} fill={`${color}18`} />
      <path
        d={visiblePts
          .map((p, i) => `${i === 0 ? "M" : "L"}${p[0]},${p[1]}`)
          .join(" ")}
        fill="none"
        stroke={color}
        strokeWidth={3}
        filter={`drop-shadow(0 0 8px ${color})`}
      />
      {visiblePts.length > 0 && (
        <circle
          cx={visiblePts[visiblePts.length - 1][0]}
          cy={visiblePts[visiblePts.length - 1][1]}
          r={6}
          fill={color}
          filter={`drop-shadow(0 0 10px ${color})`}
        />
      )}
    </svg>
  );
};

// ─── Horizontal metric bar ────────────────────────────────────────
export const MetricBar: React.FC<{
  label: string;
  value: number; // 0-1
  color?: string;
  width?: number;
}> = ({ label, value, color = CYAN, width = 500 }) => (
  <div style={{ width, marginBottom: 12 }}>
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        color: WHITE,
        fontSize: 22,
        fontFamily: "'Arial', sans-serif",
        fontWeight: 700,
        marginBottom: 4,
      }}
    >
      <span>{label}</span>
      <span style={{ color }}>{Math.round(value * 100)}%</span>
    </div>
    <div
      style={{
        height: 8,
        background: "rgba(255,255,255,0.1)",
        borderRadius: 4,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          height: "100%",
          width: `${value * 100}%`,
          background: color,
          borderRadius: 4,
          boxShadow: `0 0 10px ${color}`,
        }}
      />
    </div>
  </div>
);

// ─── Dollar sign rain ─────────────────────────────────────────────
export const MoneyRain: React.FC<{ count?: number; frame: number }> = ({
  count = 20,
  frame,
}) => (
  <>
    {Array.from({ length: count }, (_, i) => {
      const startF = i * 3;
      const speed = 18 + (i % 7) * 4;
      const x = ((i * 137) % 900) + 90;
      const y = ((frame - startF) * speed) % 2200 - 200;
      const opacity = interpolate(
        y,
        [-200, 0, 1600, 1920],
        [0, 0.8, 0.8, 0],
        { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
      );
      const size = 28 + (i % 5) * 8;
      return (
        <div
          key={i}
          style={{
            position: "absolute",
            left: x,
            top: y,
            fontSize: size,
            color: GOLD,
            opacity,
            fontWeight: 900,
            textShadow: `0 0 15px ${GOLD}`,
            pointerEvents: "none",
            userSelect: "none",
          }}
        >
          $
        </div>
      );
    })}
  </>
);
