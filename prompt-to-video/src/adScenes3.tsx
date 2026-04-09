import React from "react";
import { AbsoluteFill, interpolate, spring, useVideoConfig } from "remotion";
import {
  RED, CYAN, WHITE, GOLD, CHROME_GRAD,
  NeonText, GlitchText, GlitchBars,
  Scanlines, Grain, Vignette, Particles,
  RiseGraph, MoneyRain, useShake, useCounter,
} from "./adEffects";

// ─── Scene 9 (810–900): "Scripts, production, music, VO — done for you" ─────
export const Scene9: React.FC<{ localFrame: number }> = ({ localFrame: f }) => {
  const { fps } = useVideoConfig();

  const words = ["SCRIPTS.", "PRODUCTION.", "MUSIC.", "VOICEOVER.", "DONE FOR YOU."];
  const colors = [CYAN, WHITE, GOLD, CYAN, RED];

  return (
    <AbsoluteFill style={{ background: "#020206", overflow: "hidden" }}>
      {/* Radial bg pulse */}
      <div style={{
        position: "absolute", inset: 0,
        background: `radial-gradient(ellipse at 50% 50%, rgba(0,255,255,0.07) 0%, transparent 65%)`,
        opacity: 0.5 + Math.sin(f * 0.15) * 0.5,
      }} />

      {/* Kinetic word stack */}
      <div style={{
        position: "absolute",
        top: "50%",
        left: "50%",
        transform: "translate(-50%, -50%)",
        textAlign: "center",
        width: 960,
      }}>
        {words.map((word, i) => {
          const startF = i * 14;
          const wordEntry = spring({
            frame: Math.max(0, f - startF),
            fps,
            config: { damping: 11, stiffness: 220 },
            durationInFrames: 18,
          });
          const offsetY = interpolate(wordEntry, [0, 1], [60, 0]);
          const scale = interpolate(wordEntry, [0, 1], [0.6, 1]);
          const isDoneForYou = i === 4;

          return (
            <div key={i} style={{
              transform: `translateY(${offsetY}px) scale(${scale})`,
              opacity: wordEntry,
              marginBottom: isDoneForYou ? 0 : 4,
            }}>
              {isDoneForYou ? (
                <NeonText color={RED} style={{
                  fontSize: 88,
                  fontWeight: 900,
                  fontFamily: "'Arial Black','Arial',sans-serif",
                  letterSpacing: -1,
                  lineHeight: 1.15,
                }}>
                  {word}
                </NeonText>
              ) : (
                <div style={{
                  fontSize: 62,
                  fontWeight: 900,
                  fontFamily: "'Arial Black','Arial',sans-serif",
                  color: colors[i],
                  lineHeight: 1.2,
                  textShadow: `0 0 30px ${colors[i]}88`,
                  letterSpacing: -1,
                }}>
                  {word}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Orbiting product-shot thumbnails */}
      {[0, 1, 2, 3].map((i) => {
        const angle = (f * 0.8 + i * 90) * (Math.PI / 180);
        const rx = 430, ry = 260;
        const cx = 540 + Math.cos(angle) * rx;
        const cy = 960 + Math.sin(angle) * ry;
        const visible = interpolate(f, [i * 8, i * 8 + 20], [0, 1], {
          extrapolateLeft: "clamp", extrapolateRight: "clamp",
        });
        const thumbColors = [
          ["#1a1a2e", CYAN],
          ["#1a0a0a", RED],
          ["#0a1a0a", GOLD],
          ["#0a0a1a", "#8888FF"],
        ];
        return (
          <div key={i} style={{
            position: "absolute",
            left: cx - 55,
            top: cy - 40,
            width: 110,
            height: 80,
            background: thumbColors[i][0],
            border: `2px solid ${thumbColors[i][1]}`,
            borderRadius: 6,
            opacity: visible * 0.75,
            boxShadow: `0 0 20px ${thumbColors[i][1]}44`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 22,
          }}>
            {["🎬", "🎵", "✍️", "🎙️"][i]}
          </div>
        );
      })}

      <Scanlines />
      <Grain />
      <GlitchBars intensity={0.3} />
      <Vignette />
    </AbsoluteFill>
  );
};

// ─── Scene 10 (900–1020): "$0 → $100k revenue counter" ───────────
export const Scene10: React.FC<{ localFrame: number }> = ({ localFrame: f }) => {
  const counterVal = useCounter(0, 100000, 0, 90, f);
  const graphProgress = interpolate(f, [10, 90], [0, 1], { extrapolateRight: "clamp" });

  // Explosion at start
  const burst = interpolate(f, [0, 30], [0, 1], { extrapolateRight: "clamp" });

  const counterScale = spring({
    frame: f,
    fps: 30,
    config: { damping: 12, stiffness: 180 },
    durationInFrames: 25,
  });

  return (
    <AbsoluteFill style={{ background: "#040208", overflow: "hidden" }}>
      {/* Gold radial glow */}
      <div style={{
        position: "absolute", inset: 0,
        background: `radial-gradient(ellipse at 50% 40%, rgba(255,215,0,${burst * 0.15}) 0%, transparent 65%)`,
      }} />

      {/* Money rain */}
      <MoneyRain count={28} frame={f} />

      {/* Particles burst */}
      <Particles count={36} colors={[GOLD, WHITE, CYAN]} startFrame={0} duration={60} cx={540} cy={700} radius={900} />

      {/* Revenue counter */}
      <div style={{
        position: "absolute",
        top: 220,
        left: "50%",
        transform: `translateX(-50%) scale(${counterScale})`,
        textAlign: "center",
      }}>
        <div style={{
          fontSize: 30,
          fontWeight: 700,
          color: "rgba(255,255,255,0.5)",
          fontFamily: "'Arial',sans-serif",
          letterSpacing: 5,
          textTransform: "uppercase",
          marginBottom: 12,
        }}>
          First Month Revenue
        </div>

        <NeonText color={GOLD} style={{
          fontSize: 110,
          fontWeight: 900,
          fontFamily: "'Arial Black','Arial',sans-serif",
          letterSpacing: -2,
          lineHeight: 1,
        }}>
          ${counterVal}
        </NeonText>

        {/* Milestone labels */}
        {f > 30 && (
          <div style={{
            display: "flex",
            gap: 30,
            justifyContent: "center",
            marginTop: 20,
            opacity: interpolate(f, [30, 50], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
          }}>
            {[["MONTH 1", GOLD], ["NEW BRANDS", CYAN], ["PROVEN", WHITE]].map(([label, color]) => (
              <div key={label} style={{
                padding: "6px 18px",
                border: `2px solid ${color}`,
                borderRadius: 4,
                fontSize: 18,
                fontWeight: 700,
                color,
                fontFamily: "'Arial',sans-serif",
                letterSpacing: 2,
                boxShadow: `0 0 15px ${color}44`,
              }}>
                {label}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Skyrocketing graph */}
      <div style={{
        position: "absolute",
        top: 660,
        left: 60,
        opacity: interpolate(f, [15, 40], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
      }}>
        <div style={{
          fontSize: 22,
          color: "rgba(255,215,0,0.6)",
          fontFamily: "'Arial',sans-serif",
          marginBottom: 8,
          letterSpacing: 3,
          textTransform: "uppercase",
        }}>
          Revenue Trajectory
        </div>
        <RiseGraph width={960} height={280} progress={graphProgress} color={GOLD} />
      </div>

      {/* Social proof stat */}
      <div style={{
        position: "absolute",
        bottom: 100,
        left: 0, right: 0,
        textAlign: "center",
        opacity: interpolate(f, [60, 85], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
      }}>
        <div style={{
          fontSize: 38,
          fontWeight: 700,
          color: WHITE,
          fontFamily: "'Arial',sans-serif",
          lineHeight: 1.5,
          textShadow: "0 2px 20px rgba(0,0,0,0.9)",
        }}>
          Brands running our creative hit
          <br />
          <span style={{ color: GOLD, fontSize: 52, fontWeight: 900 }}>$100,000</span>
          <span style={{ color: WHITE }}> their first month.</span>
        </div>
      </div>

      <Scanlines />
      <Grain />
      <GlitchBars intensity={0.4} />
      <Vignette />
    </AbsoluteFill>
  );
};

// ─── Scene 11 (1020–1140): "DM SCALE — break your ceiling" ───────
export const Scene11: React.FC<{ localFrame: number }> = ({ localFrame: f }) => {
  const { fps } = useVideoConfig();
  const shakeStyle = useShake(
    interpolate(f, [0, 25], [1.5, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" })
  );

  // Ceiling shattering at start
  const shatterT = interpolate(f, [0, 50], [0, 1], { extrapolateRight: "clamp" });
  const lightBurst = interpolate(f, [20, 50], [0, 1], { extrapolateRight: "clamp" });

  // "DM SCALE" text slam
  const textEntry = spring({
    frame: Math.max(0, f - 30),
    fps,
    config: { damping: 9, stiffness: 280 },
    durationInFrames: 22,
  });
  const textScale = interpolate(textEntry, [0, 1], [3.0, 1]);

  // Electric cracks
  const cracks = [
    { x1: 540, y1: 500, x2: 200, y2: 300 },
    { x1: 540, y1: 500, x2: 880, y2: 250 },
    { x1: 540, y1: 500, x2: 100, y2: 600 },
    { x1: 540, y1: 500, x2: 980, y2: 550 },
    { x1: 540, y1: 500, x2: 350, y2: 800 },
    { x1: 540, y1: 500, x2: 750, y2: 780 },
  ];

  // Pulsing for DM SCALE
  const pulse = Math.sin(f * 0.18) * 0.08 + 1;

  return (
    <AbsoluteFill style={{ background: "#020204", overflow: "hidden", ...shakeStyle }}>
      {/* Ceiling pieces flying apart */}
      {Array.from({ length: 10 }, (_, i) => {
        const angle = (i / 10) * Math.PI - Math.PI * 0.5 + (i - 5) * 0.4;
        const speed = 200 + i * 40;
        const ox = Math.cos(angle) * speed * shatterT;
        const oy = Math.sin(angle) * speed * shatterT - shatterT * 300;
        const rot = (i - 5) * 20 * shatterT;
        const op = interpolate(shatterT, [0.6, 1], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        return (
          <div key={i} style={{
            position: "absolute",
            top: 0,
            left: `${8 + i * 9}%`,
            width: "9%",
            height: 150,
            background: "linear-gradient(180deg, #1e1e1e, #2e2e2e)",
            border: "1px solid rgba(200,200,200,0.2)",
            transform: `translateX(${ox}px) translateY(${oy}px) rotate(${rot}deg)`,
            opacity: op,
          }} />
        );
      })}

      {/* White light bursting through hole */}
      <div style={{
        position: "absolute",
        top: -200,
        left: "50%",
        transform: "translateX(-50%)",
        width: 800,
        height: 800,
        background: `radial-gradient(ellipse, rgba(255,255,255,${lightBurst * 0.6}) 0%, transparent 70%)`,
        pointerEvents: "none",
      }} />

      {/* Electric crack SVG */}
      <svg style={{ position: "absolute", inset: 0, opacity: interpolate(f, [20, 60], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) }} width={1080} height={1920}>
        {cracks.map((c, i) => {
          const progress = interpolate(f, [25 + i * 4, 45 + i * 4], [0, 1], {
            extrapolateLeft: "clamp", extrapolateRight: "clamp",
          });
          const ex = c.x1 + (c.x2 - c.x1) * progress;
          const ey = c.y1 + (c.y2 - c.y1) * progress;
          const crackColor = i % 2 === 0 ? CYAN : RED;
          return (
            <line key={i}
              x1={c.x1} y1={c.y1} x2={ex} y2={ey}
              stroke={crackColor}
              strokeWidth={2 - i * 0.1}
              opacity={0.8}
              filter={`url(#glow${i % 2})`}
            />
          );
        })}
        <defs>
          <filter id="glow0"><feGaussianBlur stdDeviation="4" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
          <filter id="glow1"><feGaussianBlur stdDeviation="4" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        </defs>
      </svg>

      {/* Particles from shattering */}
      <Particles count={48} colors={[WHITE, CYAN, RED, GOLD]} startFrame={0} duration={55} cx={540} cy={100} radius={1200} />

      {/* "DM SCALE" slam */}
      <div style={{
        position: "absolute",
        top: "42%",
        left: "50%",
        transform: `translate(-50%, -50%) scale(${textScale * pulse})`,
        textAlign: "center",
        opacity: textEntry,
        zIndex: 10,
      }}>
        <GlitchText active={f > 70} style={{
          fontSize: 148,
          fontWeight: 900,
          fontFamily: "'Arial Black','Arial',sans-serif",
          background: CHROME_GRAD,
          WebkitBackgroundClip: "text",
          WebkitTextFillColor: "transparent",
          backgroundClip: "text",
          letterSpacing: -3,
          lineHeight: 1,
          filter: `drop-shadow(0 0 40px ${CYAN}) drop-shadow(0 0 80px ${RED})`,
        }}>
          DM SCALE
        </GlitchText>

        <div style={{
          marginTop: 20,
          fontSize: 38,
          fontWeight: 700,
          color: "rgba(255,255,255,0.7)",
          fontFamily: "'Arial',sans-serif",
          letterSpacing: 4,
          textShadow: `0 0 20px ${CYAN}88`,
          opacity: interpolate(f, [50, 70], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
        }}>
          Break your ceiling.
        </div>
      </div>

      {/* CTA arrow */}
      <div style={{
        position: "absolute",
        bottom: 180,
        left: "50%",
        transform: `translateX(-50%) translateY(${Math.sin(f * 0.12) * 10}px)`,
        fontSize: 60,
        opacity: interpolate(f, [70, 90], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
        filter: `drop-shadow(0 0 15px ${CYAN})`,
      }}>
        ↑
      </div>

      <div style={{
        position: "absolute",
        bottom: 80,
        left: 0, right: 0,
        textAlign: "center",
        opacity: interpolate(f, [70, 90], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
      }}>
        <div style={{
          fontSize: 32,
          fontWeight: 700,
          color: "rgba(255,255,255,0.5)",
          fontFamily: "'Arial',sans-serif",
          letterSpacing: 2,
        }}>
          DM me the word <span style={{ color: CYAN }}>SCALE</span>
        </div>
      </div>

      <Scanlines />
      <Grain />
      <GlitchBars intensity={1.0} />
      <Vignette />
    </AbsoluteFill>
  );
};

// ─── Scene 12 (1140–1260): End card ──────────────────────────────
export const Scene12: React.FC<{ localFrame: number }> = ({ localFrame: f }) => {
  const pulse = 1 + Math.sin(f * 0.14) * 0.04;
  const glitchFlash = Math.sin(f * 0.8) > 0.92;

  // Final glitch-to-black transition
  const fadeOut = interpolate(f, [100, 120], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ background: "#010103", overflow: "hidden" }}>
      {/* Soft radial bg */}
      <div style={{
        position: "absolute", inset: 0,
        background: `radial-gradient(ellipse at 50% 50%, rgba(0,255,255,0.06) 0%, transparent 60%)`,
        opacity: 0.6 + Math.sin(f * 0.1) * 0.4,
      }} />

      {/* Grid */}
      <div style={{
        position: "absolute", inset: 0,
        backgroundImage: `
          linear-gradient(rgba(0,255,255,0.03) 1px, transparent 1px),
          linear-gradient(90deg, rgba(0,255,255,0.03) 1px, transparent 1px)
        `,
        backgroundSize: "60px 60px",
        opacity: interpolate(f, [0, 30], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
      }} />

      {/* Logo placeholder ring */}
      <div style={{
        position: "absolute",
        top: "22%",
        left: "50%",
        transform: `translate(-50%, -50%) scale(${pulse})`,
        width: 200, height: 200,
        borderRadius: "50%",
        border: `3px solid ${CYAN}`,
        boxShadow: `0 0 40px ${CYAN}, 0 0 80px ${CYAN}44, inset 0 0 40px ${CYAN}22`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        opacity: interpolate(f, [0, 25], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
      }}>
        <div style={{
          fontSize: 30,
          fontWeight: 900,
          color: CYAN,
          fontFamily: "'Arial Black','Arial',sans-serif",
          letterSpacing: 2,
          textAlign: "center",
          textShadow: `0 0 20px ${CYAN}`,
        }}>
          YOUR
          <br />LOGO
        </div>
      </div>

      {/* "DM SCALE" pulsing center */}
      <div style={{
        position: "absolute",
        top: "50%",
        left: "50%",
        transform: `translate(-50%, -50%) scale(${pulse})`,
        textAlign: "center",
        opacity: interpolate(f, [15, 40], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
      }}>
        <div style={{
          fontSize: 130,
          fontWeight: 900,
          fontFamily: "'Arial Black','Arial',sans-serif",
          background: CHROME_GRAD,
          WebkitBackgroundClip: "text",
          WebkitTextFillColor: "transparent",
          backgroundClip: "text",
          letterSpacing: -3,
          lineHeight: 1,
          filter: `drop-shadow(0 0 30px ${CYAN}) drop-shadow(0 0 60px ${RED})`,
        }}>
          DM
          <br />
          SCALE
        </div>
      </div>

      {/* Neon echo rings */}
      {[1, 2, 3].map((ring) => {
        const delay = ring * 18;
        const ringT = interpolate(f, [delay, delay + 60], [0, 1], {
          extrapolateLeft: "clamp", extrapolateRight: "clamp",
        });
        const size = 300 + ringT * 600;
        return (
          <div key={ring} style={{
            position: "absolute",
            top: "50%",
            left: "50%",
            transform: `translate(-50%, -50%)`,
            width: size,
            height: size,
            borderRadius: "50%",
            border: `2px solid ${ring % 2 === 0 ? CYAN : RED}`,
            opacity: (1 - ringT) * 0.4,
            boxShadow: `0 0 20px ${ring % 2 === 0 ? CYAN : RED}`,
            pointerEvents: "none",
          }} />
        );
      })}

      {/* Handle/tagline */}
      <div style={{
        position: "absolute",
        bottom: 200,
        left: 0, right: 0,
        textAlign: "center",
        opacity: interpolate(f, [35, 60], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
      }}>
        <div style={{
          fontSize: 34,
          fontWeight: 700,
          color: "rgba(255,255,255,0.55)",
          fontFamily: "'Arial',sans-serif",
          letterSpacing: 3,
        }}>
          @yourhandle
        </div>
        <div style={{
          fontSize: 24,
          color: "rgba(0,255,255,0.5)",
          fontFamily: "'Arial',sans-serif",
          marginTop: 10,
          letterSpacing: 2,
        }}>
          VIDEO ADS THAT SCALE
        </div>
      </div>

      {/* Glitch flash */}
      {glitchFlash && (
        <div style={{
          position: "absolute", inset: 0,
          background: `rgba(${f % 2 === 0 ? "255,0,51" : "0,255,255"},0.06)`,
          pointerEvents: "none",
        }} />
      )}

      {/* Final fade to black */}
      <div style={{
        position: "absolute", inset: 0,
        background: "#000",
        opacity: fadeOut,
        pointerEvents: "none",
        zIndex: 990,
      }} />

      {/* Neon echo linger after fade */}
      {fadeOut > 0.5 && (
        <div style={{
          position: "absolute", inset: 0,
          background: `radial-gradient(ellipse at 50% 50%, ${CYAN}0A 0%, transparent 60%)`,
          opacity: 1 - (fadeOut - 0.5) * 2,
          zIndex: 991,
          pointerEvents: "none",
        }} />
      )}

      <Scanlines opacity={0.12} />
      <Grain />
      <GlitchBars intensity={0.2} />
    </AbsoluteFill>
  );
};
