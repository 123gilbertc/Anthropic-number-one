import React from "react";
import { AbsoluteFill, interpolate, spring, useVideoConfig } from "remotion";
import {
  RED, CYAN, WHITE, BG, CHROME_GRAD,
  NeonText, GlitchText, GlitchBars,
  Scanlines, Grain, Vignette, Particles, CrashGraph,
} from "./adEffects";

// ─── Scene 1 (0–90): "YOUR ADS ARE DYING" title card ─────────────
export const Scene1: React.FC<{ localFrame: number }> = ({ localFrame: f }) => {
  const { fps } = useVideoConfig();

  // Title slam-in
  const titleScale = spring({ frame: f, fps, config: { damping: 12, stiffness: 200 }, durationInFrames: 20 });
  const titleY = interpolate(f, [0, 15], [120, 0], { extrapolateRight: "clamp" });
  const shatterStart = 65;
  const shattering = f >= shatterStart;

  // Shatter: fragments flying
  const fragments = [
    { x: -300, y: -400, rot: -45 },
    { x: 400, y: -300, rot: 30 },
    { x: -350, y: 200, rot: -20 },
    { x: 300, y: 350, rot: 55 },
    { x: -200, y: 450, rot: -70 },
    { x: 450, y: 100, rot: 40 },
    { x: 100, y: -500, rot: 15 },
    { x: -500, y: -100, rot: -35 },
  ];
  const shatterProgress = shattering
    ? interpolate(f, [shatterStart, shatterStart + 25], [0, 1], { extrapolateRight: "clamp" })
    : 0;

  // ROAS metrics streaking
  const metrics = ["ROAS ↓ -72%", "CTR ↓ -58%", "CONV ↓ -81%", "CPM ↑ +340%"];

  return (
    <AbsoluteFill style={{ background: BG, overflow: "hidden" }}>
      {/* Grid lines */}
      <div style={{
        position: "absolute", inset: 0,
        backgroundImage: `
          linear-gradient(rgba(0,255,255,0.04) 1px, transparent 1px),
          linear-gradient(90deg, rgba(0,255,255,0.04) 1px, transparent 1px)
        `,
        backgroundSize: "80px 80px",
      }} />

      {/* Particles burst */}
      <Particles count={32} startFrame={0} duration={50} cx={540} cy={960} radius={900} />
      <Particles count={20} colors={[RED, "#FF6600"]} startFrame={5} duration={45} cx={540} cy={960} radius={700} />

      {/* Main title */}
      {!shattering ? (
        <div style={{
          position: "absolute",
          top: "50%", left: "50%",
          transform: `translate(-50%,-50%) translateY(${titleY}px) scale(${titleScale})`,
          textAlign: "center",
          width: 900,
          zIndex: 10,
        }}>
          <GlitchText active={f > 30} style={{
            fontSize: 118,
            fontWeight: 900,
            fontFamily: "'Arial Black', 'Arial', sans-serif",
            color: WHITE,
            letterSpacing: -2,
            lineHeight: 1.0,
            background: CHROME_GRAD,
            WebkitBackgroundClip: "text",
            WebkitTextFillColor: "transparent",
            backgroundClip: "text",
            filter: "drop-shadow(0 0 30px rgba(255,0,51,0.8)) drop-shadow(0 0 60px rgba(0,255,255,0.4))",
          }}>
            YOUR ADS ARE DYING
          </GlitchText>

          {/* Red underline */}
          <div style={{
            height: 6, background: RED,
            width: interpolate(f, [20, 50], [0, 860], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
            margin: "16px auto 0",
            boxShadow: `0 0 20px ${RED}, 0 0 40px ${RED}`,
          }} />
        </div>
      ) : (
        // Shattered fragments
        fragments.map((frag, i) => {
          const delay = i * 0.08;
          const localT = Math.max(0, shatterProgress - delay);
          const ox = interpolate(localT, [0, 1], [0, frag.x], { extrapolateRight: "clamp" });
          const oy = interpolate(localT, [0, 1], [0, frag.y], { extrapolateRight: "clamp" });
          const rot = interpolate(localT, [0, 1], [0, frag.rot], { extrapolateRight: "clamp" });
          const op = interpolate(localT, [0.4, 1], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
          const WORDS = ["YOUR", "ADS", "ARE", "DYI", "NG", "!", "●", "◆"];
          return (
            <div key={i} style={{
              position: "absolute",
              top: "50%", left: "50%",
              transform: `translate(calc(-50% + ${ox}px), calc(-50% + ${oy}px)) rotate(${rot}deg)`,
              opacity: op,
              fontSize: 80 - i * 5,
              fontWeight: 900,
              fontFamily: "'Arial Black', 'Arial', sans-serif",
              background: CHROME_GRAD,
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
              backgroundClip: "text",
              filter: `drop-shadow(0 0 20px ${i % 2 === 0 ? RED : CYAN})`,
              userSelect: "none",
            }}>
              {WORDS[i]}
            </div>
          );
        })
      )}

      {/* Streaming metrics */}
      {metrics.map((m, i) => {
        const startF = 20 + i * 12;
        const t = interpolate(f, [startF, startF + 60], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        const x = interpolate(t, [0, 1], [-400, 1200]);
        const y = 300 + i * 160 + (i % 2 === 0 ? 0 : 80);
        return (
          <div key={i} style={{
            position: "absolute",
            top: y,
            left: x,
            fontSize: 38,
            fontWeight: 800,
            fontFamily: "'Arial', sans-serif",
            color: RED,
            opacity: 0.75,
            textShadow: `0 0 15px ${RED}`,
            whiteSpace: "nowrap",
            pointerEvents: "none",
          }}>
            {m}
          </div>
        );
      })}

      <Scanlines />
      <Grain />
      <GlitchBars intensity={0.6} />
      <Vignette />
    </AbsoluteFill>
  );
};

// ─── Scene 2 (90–180): ROAS Drop ─────────────────────────────────
export const Scene2: React.FC<{ localFrame: number }> = ({ localFrame: f }) => {
  const graphProgress = interpolate(f, [0, 60], [0, 1], { extrapolateRight: "clamp" });

  // Stacked dollar bills flying up then crashing
  const billsUp = interpolate(f, [0, 25], [0, 1], { extrapolateRight: "clamp" });
  const billsCrash = interpolate(f, [30, 60], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  const caption = "You scaled your ads to $100/day\nand your ROAS dropped.";

  return (
    <AbsoluteFill style={{ background: "#040408", overflow: "hidden" }}>
      {/* Subtle red grid */}
      <div style={{
        position: "absolute", inset: 0,
        backgroundImage: "linear-gradient(rgba(255,0,51,0.05) 1px, transparent 1px), linear-gradient(90deg,rgba(255,0,51,0.05) 1px,transparent 1px)",
        backgroundSize: "60px 60px",
      }} />

      {/* Dollar bills stack */}
      {Array.from({ length: 8 }, (_, i) => {
        const baseX = 540 - 60 + (i % 3 - 1) * 30;
        const baseY = billsUp > 0
          ? 1100 - billsUp * 400 + (billsCrash > 0 ? billsCrash * 700 : 0) + i * 18
          : 1100 + i * 18;
        const rot = (i - 3.5) * 5;
        const op = interpolate(billsCrash, [0.85, 1], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        return (
          <div key={i} style={{
            position: "absolute",
            left: baseX,
            top: baseY,
            width: 130,
            height: 60,
            background: "linear-gradient(135deg, #1a5c1a, #2d8a2d, #1a5c1a)",
            border: "2px solid #3aad3a",
            borderRadius: 4,
            transform: `rotate(${rot}deg)`,
            opacity: op,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 28,
            fontWeight: 900,
            color: "#5aff5a",
            boxShadow: "0 4px 20px rgba(0,180,0,0.4)",
          }}>
            $100
          </div>
        );
      })}

      {/* ROAS label */}
      <div style={{
        position: "absolute",
        top: 180,
        left: 80,
        fontSize: 26,
        fontWeight: 700,
        color: "rgba(255,255,255,0.5)",
        fontFamily: "'Arial', sans-serif",
        letterSpacing: 3,
        textTransform: "uppercase",
      }}>
        ROAS Performance
      </div>

      {/* Crash graph */}
      <div style={{ position: "absolute", top: 220, left: 60 }}>
        <CrashGraph width={960} height={380} progress={graphProgress} color={RED} />
      </div>

      {/* Y-axis labels */}
      {["4.2×", "3.1×", "2.0×", "0.8×"].map((label, i) => (
        <div key={i} style={{
          position: "absolute",
          top: 225 + i * 95,
          left: 16,
          fontSize: 20,
          color: i < 2 ? "rgba(0,255,255,0.6)" : "rgba(255,0,51,0.7)",
          fontFamily: "'Arial', sans-serif",
          fontWeight: 700,
        }}>
          {label}
        </div>
      ))}

      {/* Big ROAS number crashing */}
      <NeonText color={RED} style={{
        position: "absolute",
        top: 640,
        left: "50%",
        transform: "translateX(-50%)",
        fontSize: 90,
        fontWeight: 900,
        fontFamily: "'Arial Black','Arial',sans-serif",
        textAlign: "center",
        opacity: interpolate(f, [40, 55], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
      }}>
        ROAS: 0.6×
      </NeonText>

      {/* Caption */}
      <div style={{
        position: "absolute",
        bottom: 160,
        left: 0, right: 0,
        textAlign: "center",
        padding: "0 60px",
        opacity: interpolate(f, [0, 20], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
      }}>
        {caption.split("\n").map((line, i) => (
          <div key={i} style={{
            fontSize: 42,
            fontWeight: 700,
            color: WHITE,
            fontFamily: "'Arial', sans-serif",
            lineHeight: 1.3,
            textShadow: "0 2px 20px rgba(0,0,0,0.9)",
          }}>
            {line}
          </div>
        ))}
      </div>

      <Scanlines />
      <Grain />
      <GlitchBars intensity={0.4} />
      <Vignette />
    </AbsoluteFill>
  );
};

// ─── Scene 3 (180–270): Budget Doubled ───────────────────────────
export const Scene3: React.FC<{ localFrame: number }> = ({ localFrame: f }) => {
  const budgets = ["$100", "$200", "$400"];
  const visibleBudgets = f < 20 ? 1 : f < 40 ? 2 : 3;

  const graphProgress = interpolate(f, [20, 80], [0, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ background: "#05050A", overflow: "hidden" }}>
      {/* Split line down middle (digital wipe) */}
      <div style={{
        position: "absolute",
        top: 0, bottom: 0,
        left: "50%",
        width: 3,
        background: `linear-gradient(180deg, transparent, ${CYAN}, ${RED}, ${CYAN}, transparent)`,
        opacity: 0.5,
        boxShadow: `0 0 20px ${CYAN}`,
      }} />

      {/* Budget counters */}
      <div style={{
        position: "absolute",
        top: 220,
        left: 0, right: 0,
        display: "flex",
        justifyContent: "space-around",
        alignItems: "flex-end",
        padding: "0 80px",
      }}>
        {budgets.slice(0, visibleBudgets).map((b, i) => {
          const isLast = i === visibleBudgets - 1;
          const entryF = i === 0 ? 0 : i === 1 ? 20 : 40;
          const entryT = interpolate(f, [entryF, entryF + 15], [0, 1], {
            extrapolateLeft: "clamp", extrapolateRight: "clamp",
          });
          return (
            <div key={i} style={{
              textAlign: "center",
              transform: `scale(${0.7 + entryT * 0.3})`,
              opacity: entryT,
            }}>
              <div style={{
                fontSize: 88 - i * 8,
                fontWeight: 900,
                fontFamily: "'Arial Black','Arial',sans-serif",
                color: isLast ? RED : "rgba(255,255,255,0.4)",
                textShadow: isLast ? `0 0 40px ${RED}, 0 0 80px ${RED}` : "none",
                lineHeight: 1,
              }}>
                {b}
              </div>
              <div style={{
                fontSize: 22,
                color: "rgba(255,255,255,0.4)",
                fontFamily: "'Arial',sans-serif",
                marginTop: 8,
              }}>
                {i === 0 ? "Day 1" : i === 1 ? "Day 3" : "Day 7"}
              </div>
              {i < visibleBudgets - 1 && (
                <div style={{
                  position: "absolute",
                  right: -50,
                  top: "40%",
                  fontSize: 50,
                  color: CYAN,
                  opacity: 0.7,
                }}>→</div>
              )}
            </div>
          );
        })}
      </div>

      {/* Flatline graph */}
      <div style={{
        position: "absolute",
        top: 560,
        left: 60,
        opacity: interpolate(f, [30, 50], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
      }}>
        <div style={{
          fontSize: 24,
          color: "rgba(255,255,255,0.5)",
          fontFamily: "'Arial',sans-serif",
          marginBottom: 12,
          letterSpacing: 3,
          textTransform: "uppercase",
        }}>
          ROAS (Still Crashing)
        </div>
        <CrashGraph width={960} height={250} progress={graphProgress} color={RED} />
      </div>

      {/* Audience avatars fading */}
      <div style={{
        position: "absolute",
        bottom: 280,
        left: 0, right: 0,
        display: "flex",
        justifyContent: "center",
        gap: 30,
      }}>
        {Array.from({ length: 7 }, (_, i) => {
          const fadeDelay = 40 + i * 5;
          const opacity = interpolate(f, [fadeDelay, fadeDelay + 20], [1, 0.15], {
            extrapolateLeft: "clamp", extrapolateRight: "clamp",
          });
          return (
            <div key={i} style={{
              width: 60, height: 80,
              borderRadius: "50% 50% 0 0",
              background: `rgba(${i % 2 === 0 ? "0,255,255" : "255,0,51"},0.6)`,
              opacity,
              boxShadow: `0 0 15px rgba(${i % 2 === 0 ? "0,255,255" : "255,0,51"},0.3)`,
              position: "relative",
            }}>
              <div style={{
                position: "absolute",
                top: -30,
                left: "50%",
                transform: "translateX(-50%)",
                width: 40,
                height: 40,
                borderRadius: "50%",
                background: `rgba(${i % 2 === 0 ? "0,255,255" : "255,0,51"},0.7)`,
              }} />
            </div>
          );
        })}
      </div>

      {/* Caption */}
      <div style={{
        position: "absolute",
        bottom: 100,
        left: 0, right: 0,
        textAlign: "center",
        padding: "0 60px",
      }}>
        <div style={{
          fontSize: 44,
          fontWeight: 700,
          color: WHITE,
          fontFamily: "'Arial',sans-serif",
          lineHeight: 1.3,
          textShadow: "0 2px 20px rgba(0,0,0,0.9)",
        }}>
          You doubled the budget.
        </div>
        <div style={{
          fontSize: 44,
          fontWeight: 700,
          color: RED,
          fontFamily: "'Arial',sans-serif",
          textShadow: `0 0 30px ${RED}`,
          marginTop: 8,
        }}>
          It got worse.
        </div>
      </div>

      <Scanlines />
      <Grain />
      <GlitchBars intensity={0.7} />
      <Vignette />
    </AbsoluteFill>
  );
};

// ─── Scene 4 (270–360): The Executives ───────────────────────────
export const Scene4: React.FC<{ localFrame: number }> = ({ localFrame: f }) => {
  const { fps } = useVideoConfig();

  // Two executive holographic frames
  const exec1Enter = spring({ frame: f, fps, config: { damping: 14, stiffness: 180 }, durationInFrames: 25 });
  const exec2Enter = spring({ frame: Math.max(0, f - 15), fps, config: { damping: 14, stiffness: 180 }, durationInFrames: 25 });

  // Glitch their faces at the end
  const glitchStart = 65;
  const glitching = f >= glitchStart;
  const glitchT = glitching ? interpolate(f, [glitchStart, glitchStart + 25], [0, 1], { extrapolateRight: "clamp" }) : 0;

  const Exec: React.FC<{ side: "left" | "right"; enter: number; title: string; quote: string }> = ({
    side, enter, title, quote,
  }) => {
    const x = side === "left" ? -60 : 60;
    const frameX = interpolate(enter, [0, 1], [side === "left" ? -400 : 400, x], { extrapolateRight: "clamp" });
    const glitchOffset = glitching ? Math.sin(f * 7.3) * 12 * glitchT : 0;
    const crackOpacity = interpolate(glitchT, [0.3, 1], [0, 1], { extrapolateLeft: "clamp" });

    return (
      <div style={{
        position: "absolute",
        top: "50%",
        left: side === "left" ? "25%" : "75%",
        transform: `translate(calc(-50% + ${frameX}px), calc(-50% + ${glitchOffset}px)) scale(${enter})`,
        width: 320,
        opacity: interpolate(glitchT, [0.7, 1], [1, 0], { extrapolateLeft: "clamp" }),
      }}>
        {/* Holographic frame border */}
        <div style={{
          border: `2px solid ${CYAN}`,
          borderRadius: 12,
          padding: 20,
          background: "rgba(0,255,255,0.04)",
          boxShadow: `0 0 30px ${CYAN}44, inset 0 0 30px ${CYAN}11`,
          position: "relative",
          overflow: "hidden",
        }}>
          {/* Corner decorations */}
          {[0, 1, 2, 3].map((c) => (
            <div key={c} style={{
              position: "absolute",
              width: 20, height: 20,
              borderTop: c < 2 ? `3px solid ${CYAN}` : "none",
              borderBottom: c >= 2 ? `3px solid ${CYAN}` : "none",
              borderLeft: c % 2 === 0 ? `3px solid ${CYAN}` : "none",
              borderRight: c % 2 === 1 ? `3px solid ${CYAN}` : "none",
              top: c < 2 ? -2 : undefined,
              bottom: c >= 2 ? -2 : undefined,
              left: c % 2 === 0 ? -2 : undefined,
              right: c % 2 === 1 ? -2 : undefined,
            }} />
          ))}

          {/* Suit body */}
          <div style={{
            width: 100, height: 120,
            margin: "0 auto 16px",
            background: "linear-gradient(180deg, #2a2a2a, #1a1a1a)",
            borderRadius: "50% 50% 0 0",
            position: "relative",
            border: "2px solid rgba(0,255,255,0.3)",
          }}>
            {/* Head */}
            <div style={{
              position: "absolute",
              top: -50,
              left: "50%",
              transform: "translateX(-50%)",
              width: 60,
              height: 60,
              borderRadius: "50%",
              background: "linear-gradient(135deg, #3a3a3a, #252525)",
              border: "2px solid rgba(0,255,255,0.3)",
            }} />
            {/* Tie */}
            <div style={{
              position: "absolute",
              top: 10,
              left: "50%",
              transform: "translateX(-50%)",
              width: 16,
              height: 60,
              background: RED,
              clipPath: "polygon(30% 0%, 70% 0%, 100% 100%, 0% 100%)",
            }} />
          </div>

          <div style={{
            textAlign: "center",
            color: CYAN,
            fontSize: 18,
            fontWeight: 700,
            fontFamily: "'Arial',sans-serif",
            letterSpacing: 2,
            marginBottom: 12,
          }}>
            {title}
          </div>
          <div style={{
            textAlign: "center",
            color: WHITE,
            fontSize: 22,
            fontFamily: "'Arial',sans-serif",
            fontStyle: "italic",
            lineHeight: 1.4,
            opacity: 0.9,
          }}>
            "{quote}"
          </div>

          {/* Shrug animation */}
          <div style={{
            position: "absolute",
            top: 10, right: 10,
            fontSize: 40,
            opacity: interpolate(f, [20, 35], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
            transform: `rotate(${Math.sin(f * 0.2) * 10}deg)`,
          }}>
            🤷
          </div>
        </div>

        {/* Crack overlay */}
        {crackOpacity > 0 && (
          <div style={{
            position: "absolute",
            inset: 0,
            opacity: crackOpacity,
            background: `radial-gradient(circle, transparent 30%, ${RED}33 100%)`,
            borderRadius: 12,
            border: `3px solid ${RED}`,
          }}>
            <div style={{
              position: "absolute",
              top: "20%", left: "10%",
              width: "80%",
              height: 3,
              background: RED,
              transform: `rotate(${-15 + glitchT * 5}deg)`,
              opacity: 0.7,
            }} />
          </div>
        )}
      </div>
    );
  };

  return (
    <AbsoluteFill style={{ background: "#030308", overflow: "hidden" }}>
      <div style={{
        position: "absolute", inset: 0,
        backgroundImage: "linear-gradient(rgba(0,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(0,255,255,0.03) 1px,transparent 1px)",
        backgroundSize: "80px 80px",
      }} />

      <Exec side="left" enter={exec1Enter} title="MEDIA BUYER" quote="Targeting is fine." />
      <Exec side="right" enter={exec2Enter} title="PRODUCT MGR" quote="Offer is fine." />

      {/* VS divider */}
      <div style={{
        position: "absolute",
        top: "50%",
        left: "50%",
        transform: "translate(-50%,-50%)",
        fontSize: 48,
        fontWeight: 900,
        color: "rgba(255,255,255,0.1)",
        fontFamily: "'Arial Black','Arial',sans-serif",
      }}>
        &
      </div>

      {/* Caption */}
      <div style={{
        position: "absolute",
        bottom: 100,
        left: 0, right: 0,
        textAlign: "center",
        padding: "0 60px",
        opacity: interpolate(f, [0, 20], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
      }}>
        <div style={{
          fontSize: 38,
          fontWeight: 700,
          color: WHITE,
          fontFamily: "'Arial',sans-serif",
          lineHeight: 1.4,
          textShadow: "0 2px 20px rgba(0,0,0,0.9)",
        }}>
          Your media buyer said targeting is fine.
          <br />Your PM said the offer is fine.
        </div>
      </div>

      <Scanlines />
      <Grain />
      <GlitchBars intensity={glitching ? 1.5 : 0.3} />
      <Vignette />
    </AbsoluteFill>
  );
};
