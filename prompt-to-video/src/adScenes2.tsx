import React from "react";
import { AbsoluteFill, interpolate, spring, useVideoConfig } from "remotion";
import {
  RED, CYAN, WHITE, GOLD,
  NeonText, GlitchText, GlitchBars,
  Scanlines, Grain, Vignette, Particles, CrashGraph,
  useShake,
} from "./adEffects";

// ─── Scene 5 (360–420): "Nobody told you the truth" ──────────────
export const Scene5: React.FC<{ localFrame: number }> = ({ localFrame: f }) => {
  // Hard cut to black → white text slams in
  const textEntry = spring({
    frame: Math.max(0, f - 8),
    fps: 30,
    config: { damping: 8, stiffness: 300 },
    durationInFrames: 20,
  });
  const scaleIn = interpolate(textEntry, [0, 1], [2.2, 1]);
  const shakeStyle = useShake(interpolate(f, [8, 25], [2, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }));

  // Bass drop line that slams up
  const lineWidth = interpolate(f, [10, 30], [0, 900], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ background: "#000000", overflow: "hidden" }}>
      {/* Flash at start */}
      <div style={{
        position: "absolute", inset: 0,
        background: WHITE,
        opacity: interpolate(f, [0, 6], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
        pointerEvents: "none",
      }} />

      {/* Red slash decorations */}
      <div style={{
        position: "absolute",
        top: "30%", left: -100, right: -100,
        height: 3,
        background: `linear-gradient(90deg, transparent, ${RED}, transparent)`,
        transform: "rotate(-2deg)",
        opacity: interpolate(f, [15, 25], [0, 0.5], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
      }} />
      <div style={{
        position: "absolute",
        top: "70%", left: -100, right: -100,
        height: 3,
        background: `linear-gradient(90deg, transparent, ${RED}, transparent)`,
        transform: "rotate(2deg)",
        opacity: interpolate(f, [18, 28], [0, 0.5], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
      }} />

      {/* Main text */}
      <div style={{
        position: "absolute",
        top: "50%", left: "50%",
        transform: `translate(-50%,-50%) scale(${scaleIn})`,
        textAlign: "center",
        width: 900,
        ...shakeStyle,
      }}>
        <GlitchText active={f > 20} style={{
          fontSize: 96,
          fontWeight: 900,
          fontFamily: "'Arial Black','Arial',sans-serif",
          color: WHITE,
          letterSpacing: -1,
          lineHeight: 1.1,
          textShadow: `0 0 40px rgba(255,255,255,0.6), 0 0 80px rgba(255,255,255,0.2)`,
        }}>
          NOBODY TOLD YOU
          <br />THE TRUTH.
        </GlitchText>
      </div>

      {/* Bass-drop underline */}
      <div style={{
        position: "absolute",
        top: "62%",
        left: `${(1080 - lineWidth) / 2}px`,
        height: 6,
        width: lineWidth,
        background: `linear-gradient(90deg, ${RED}, ${CYAN}, ${RED})`,
        boxShadow: `0 0 30px ${RED}, 0 0 60px ${CYAN}`,
      }} />

      {/* Particles burst */}
      <Particles count={40} startFrame={8} duration={40} cx={540} cy={960} radius={1000} />

      <Scanlines opacity={0.25} />
      <Grain />
      <GlitchBars intensity={1.2} />
    </AbsoluteFill>
  );
};

// ─── Scene 6 (420–540): "Your creative hit its ceiling" ──────────
export const Scene6: React.FC<{ localFrame: number }> = ({ localFrame: f }) => {
  // Ceiling descends from top
  const ceilingY = interpolate(f, [0, 40], [-300, 80], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: (t) => 1 - Math.pow(1 - t, 3),
  });

  // Ad creative (looping rectangle) smashing upward repeatedly
  const hitCycle = f % 30; // hits every 30 frames
  const adY = interpolate(hitCycle, [0, 12, 18, 30], [900, 300, 420, 900], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const adScale = interpolate(hitCycle, [10, 13, 16], [1, 0.85, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Crack grows with each hit
  const hitCount = Math.floor(f / 30);
  const crackOpacity = Math.min(1, hitCount * 0.25);

  // Dust particles at impact
  const showDust = hitCycle > 10 && hitCycle < 25;

  // Ad creative desaturation over time
  const desaturation = interpolate(f, [30, 100], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ background: "#030305", overflow: "hidden" }}>
      {/* Background grid */}
      <div style={{
        position: "absolute", inset: 0,
        backgroundImage: "linear-gradient(rgba(255,0,51,0.04) 1px, transparent 1px), linear-gradient(90deg, rgba(255,0,51,0.04) 1px,transparent 1px)",
        backgroundSize: "70px 70px",
      }} />

      {/* Ceiling */}
      <div style={{
        position: "absolute",
        top: ceilingY,
        left: -20, right: -20,
        height: 220,
        background: "linear-gradient(180deg, #1a1a1a 0%, #2a2a2a 60%, #1a1a1a 100%)",
        border: `2px solid rgba(200,200,200,0.3)`,
        boxShadow: `0 20px 60px rgba(0,0,0,0.8), 0 0 40px rgba(255,0,51,0.2)`,
      }}>
        {/* Ceiling cracks */}
        {hitCount > 0 && (
          <svg
            style={{ position: "absolute", bottom: 0, left: 0, width: "100%", height: 60, opacity: crackOpacity }}
            viewBox="0 0 1120 60"
          >
            <path d="M540,60 L510,30 L480,50 L460,10" stroke={RED} strokeWidth={3} fill="none" opacity="0.8" />
            <path d="M540,60 L570,25 L600,45 L640,5" stroke={RED} strokeWidth={2} fill="none" opacity="0.6" />
            <path d="M540,60 L520,40 L500,55" stroke={RED} strokeWidth={1.5} fill="none" opacity="0.5" />
            <path d="M540,60 L560,35 L580,50" stroke={RED} strokeWidth={1.5} fill="none" opacity="0.5" />
          </svg>
        )}
        {/* Texture lines */}
        {Array.from({ length: 8 }, (_, i) => (
          <div key={i} style={{
            position: "absolute",
            top: 20 + i * 24,
            left: 0, right: 0,
            height: 1,
            background: "rgba(255,255,255,0.06)",
          }} />
        ))}
        {/* "CEILING" label */}
        <div style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%,-50%)",
          fontSize: 22,
          fontWeight: 700,
          color: "rgba(255,255,255,0.2)",
          fontFamily: "'Arial',sans-serif",
          letterSpacing: 12,
          textTransform: "uppercase",
        }}>
          CREATIVE CEILING
        </div>
      </div>

      {/* Ad creative box smashing upward */}
      <div style={{
        position: "absolute",
        left: "50%",
        top: adY,
        transform: `translateX(-50%) scale(${adScale})`,
        width: 420,
        height: 320,
        background: `rgba(20,20,40,${1 - desaturation * 0.7})`,
        border: `3px solid rgba(${desaturation > 0.5 ? "255,0,51" : "0,255,255"},${0.8 - desaturation * 0.5})`,
        borderRadius: 8,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        filter: `saturate(${1 - desaturation * 0.8}) brightness(${1 - desaturation * 0.3})`,
        boxShadow: `0 0 40px rgba(0,255,255,${0.4 - desaturation * 0.3})`,
      }}>
        {/* Fake ad content */}
        <div style={{
          width: "80%",
          height: 20,
          background: "rgba(255,255,255,0.3)",
          borderRadius: 4,
          marginBottom: 12,
        }} />
        <div style={{
          width: "60%",
          height: 14,
          background: "rgba(255,255,255,0.2)",
          borderRadius: 4,
          marginBottom: 8,
        }} />
        <div style={{
          fontSize: 32,
          fontWeight: 900,
          color: desaturation > 0.6 ? "rgba(255,0,51,0.5)" : "rgba(255,255,255,0.7)",
          fontFamily: "'Arial Black','Arial',sans-serif",
          marginTop: 12,
        }}>
          SAME AD
        </div>
        <div style={{
          fontSize: 20,
          color: "rgba(255,255,255,0.3)",
          fontFamily: "'Arial',sans-serif",
        }}>
          Week {Math.min(4, hitCount + 1)}
        </div>
        {/* Loop icon */}
        <div style={{
          position: "absolute",
          top: 8, right: 8,
          fontSize: 20,
          opacity: 0.5,
          filter: desaturation > 0.5 ? `hue-rotate(180deg)` : "none",
        }}>
          🔄
        </div>
      </div>

      {/* Dust/spark particles at impact */}
      {showDust && Array.from({ length: 12 }, (_, i) => {
        const angle = (i / 12) * Math.PI - Math.PI * 0.2;
        const speed = 30 + i * 8;
        const t = (hitCycle - 10) / 15;
        return (
          <div key={i} style={{
            position: "absolute",
            left: 540 + Math.cos(angle) * speed * t - 3,
            top: (ceilingY + 220) + Math.sin(angle) * speed * t * 0.3 - 3,
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: i % 2 === 0 ? RED : GOLD,
            opacity: 1 - t,
            boxShadow: `0 0 8px ${i % 2 === 0 ? RED : GOLD}`,
          }} />
        );
      })}

      {/* Caption */}
      <div style={{
        position: "absolute",
        bottom: 100,
        left: 0, right: 0,
        textAlign: "center",
        padding: "0 60px",
      }}>
        <NeonText color={CYAN} style={{
          fontSize: 52,
          fontWeight: 900,
          fontFamily: "'Arial Black','Arial',sans-serif",
          lineHeight: 1.2,
        }}>
          Your creative hit
          <br />its ceiling.
        </NeonText>
      </div>

      <Scanlines />
      <Grain />
      <GlitchBars intensity={0.5} />
      <Vignette />
    </AbsoluteFill>
  );
};

// ─── Scene 7 (540–690): Audience fatigue montage ─────────────────
export const Scene7: React.FC<{ localFrame: number }> = ({ localFrame: f }) => {
  // Rapid desaturation sequence – 5 fake "ad cards" that fade
  const cards = [
    { x: 160, y: 300, delay: 0 },
    { x: 680, y: 260, delay: 8 },
    { x: 380, y: 580, delay: 16 },
    { x: 100, y: 800, delay: 24 },
    { x: 700, y: 750, delay: 32 },
  ];

  const graphProgress = interpolate(f, [20, 110], [0, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ background: "#04040A", overflow: "hidden" }}>
      {/* Background */}
      <div style={{
        position: "absolute", inset: 0,
        backgroundImage: "linear-gradient(rgba(255,0,51,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,0,51,0.03) 1px,transparent 1px)",
        backgroundSize: "50px 50px",
      }} />

      {/* Fading ad cards */}
      {cards.map((card, i) => {
        const age = interpolate(f, [card.delay, card.delay + 40], [0, 1], {
          extrapolateLeft: "clamp", extrapolateRight: "clamp",
        });
        const brightness = 1 - age * 0.7;
        const saturation = 1 - age * 0.9;
        return (
          <div key={i} style={{
            position: "absolute",
            left: card.x,
            top: card.y,
            width: 220,
            height: 160,
            background: "rgba(20,20,35,0.9)",
            border: `2px solid rgba(0,255,255,${0.5 - age * 0.4})`,
            borderRadius: 8,
            padding: 16,
            filter: `saturate(${saturation}) brightness(${brightness})`,
            boxShadow: age > 0.5 ? "none" : `0 0 20px rgba(0,255,255,0.2)`,
          }}>
            {/* Looping video indicator */}
            <div style={{
              width: "100%", height: 12,
              background: `rgba(${age > 0.5 ? "100,100,100" : "0,255,255"},0.4)`,
              borderRadius: 3, marginBottom: 10,
            }} />
            <div style={{
              width: "75%", height: 8,
              background: `rgba(${age > 0.5 ? "80,80,80" : "255,255,255"},0.3)`,
              borderRadius: 3, marginBottom: 8,
            }} />
            <div style={{
              fontSize: 24,
              fontWeight: 900,
              color: age > 0.7 ? "rgba(100,100,100,0.5)" : "rgba(255,255,255,0.8)",
              fontFamily: "'Arial Black','Arial',sans-serif",
            }}>
              AD #{i + 1}
            </div>
            {/* Yawn/scroll emoji */}
            {age > 0.5 && (
              <div style={{
                position: "absolute",
                top: 8, right: 10,
                fontSize: 24,
                opacity: age - 0.5,
              }}>
                😴
              </div>
            )}
            {/* Glitch on fully faded */}
            {age > 0.85 && (
              <div style={{
                position: "absolute",
                inset: 0,
                background: `rgba(255,0,51,${(age - 0.85) * 2 * (Math.sin(f * 8) > 0.5 ? 1 : 0)})`,
                borderRadius: 8,
              }} />
            )}
          </div>
        );
      })}

      {/* Performance graph */}
      <div style={{
        position: "absolute",
        top: 950,
        left: 60,
        opacity: interpolate(f, [30, 55], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
      }}>
        <div style={{
          fontSize: 22,
          color: "rgba(255,255,255,0.4)",
          fontFamily: "'Arial',sans-serif",
          marginBottom: 8,
          letterSpacing: 3,
          textTransform: "uppercase",
        }}>
          Ad Performance Over Time
        </div>
        <CrashGraph width={960} height={200} progress={graphProgress} color={RED} />
      </div>

      {/* Audience fatigue stat */}
      <div style={{
        position: "absolute",
        top: 1220,
        left: "50%",
        transform: "translateX(-50%)",
        textAlign: "center",
        opacity: interpolate(f, [50, 75], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
      }}>
        <NeonText color={RED} style={{
          fontSize: 72,
          fontWeight: 900,
          fontFamily: "'Arial Black','Arial',sans-serif",
        }}>
          −30–50%
        </NeonText>
        <div style={{
          fontSize: 28,
          color: "rgba(255,255,255,0.6)",
          fontFamily: "'Arial',sans-serif",
          marginTop: 8,
        }}>
          performance lost after 3 weeks
        </div>
      </div>

      {/* Caption */}
      <div style={{
        position: "absolute",
        bottom: 80,
        left: 0, right: 0,
        textAlign: "center",
        padding: "0 60px",
      }}>
        <div style={{
          fontSize: 34,
          fontWeight: 700,
          color: WHITE,
          fontFamily: "'Arial',sans-serif",
          lineHeight: 1.5,
          textShadow: "0 2px 20px rgba(0,0,0,0.9)",
        }}>
          The same ad running for more than
          <span style={{ color: RED }}> 3 weeks</span> loses
          <span style={{ color: RED }}> 30–50%</span> of its
          performance as your audience fatigues.
        </div>
      </div>

      <Scanlines />
      <Grain />
      <GlitchBars intensity={0.6} />
      <Vignette />
    </AbsoluteFill>
  );
};

// ─── Scene 8 (690–810): "3 new ads, 7–12 days" ───────────────────
export const Scene8: React.FC<{ localFrame: number }> = ({ localFrame: f }) => {
  const { fps } = useVideoConfig();

  // Energy explosion at start
  const energyBurst = interpolate(f, [0, 20], [0, 1], { extrapolateRight: "clamp" });

  // Ad orbs assembly
  const orbs = [
    { x: 200, y: 700, delay: 5 },
    { x: 540, y: 650, delay: 12 },
    { x: 880, y: 700, delay: 19 },
  ];

  // "7-12 DAYS" punch in
  const daysEntry = spring({
    frame: Math.max(0, f - 65),
    fps,
    config: { damping: 10, stiffness: 250 },
    durationInFrames: 20,
  });
  const daysScale = interpolate(daysEntry, [0, 1], [2.5, 1]);

  return (
    <AbsoluteFill style={{ background: "#020208", overflow: "hidden" }}>
      {/* Burst background */}
      <div style={{
        position: "absolute",
        inset: 0,
        background: `radial-gradient(ellipse at 50% 50%, rgba(0,255,255,${energyBurst * 0.12}) 0%, transparent 70%)`,
      }} />

      {/* Energy particles */}
      <Particles count={40} startFrame={0} duration={50} cx={540} cy={960} radius={800} />
      <Particles count={24} colors={[CYAN, WHITE]} startFrame={3} duration={45} cx={540} cy={960} radius={600} />

      {/* Title */}
      <div style={{
        position: "absolute",
        top: 160,
        left: 0, right: 0,
        textAlign: "center",
        opacity: interpolate(f, [0, 20], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
      }}>
        <div style={{
          fontSize: 44,
          fontWeight: 700,
          color: "rgba(255,255,255,0.6)",
          fontFamily: "'Arial',sans-serif",
          letterSpacing: 4,
          textTransform: "uppercase",
          marginBottom: 8,
        }}>
          We build you
        </div>
        <NeonText color={CYAN} style={{
          fontSize: 100,
          fontWeight: 900,
          fontFamily: "'Arial Black','Arial',sans-serif",
          lineHeight: 1,
        }}>
          3 NEW ADS
        </NeonText>
        <div style={{
          fontSize: 36,
          color: "rgba(255,255,255,0.5)",
          fontFamily: "'Arial',sans-serif",
          marginTop: 8,
        }}>
          every time you need them
        </div>
      </div>

      {/* Spinning orbs (video ad units) */}
      {orbs.map((orb, i) => {
        const orbEntry = spring({
          frame: Math.max(0, f - orb.delay),
          fps,
          config: { damping: 12, stiffness: 200 },
          durationInFrames: 20,
        });
        const spin = (f - orb.delay) * 2;
        const glowColor = i === 1 ? CYAN : i === 0 ? RED : GOLD;
        return (
          <div key={i} style={{
            position: "absolute",
            left: orb.x - 90,
            top: orb.y - 90,
            width: 180, height: 180,
            transform: `scale(${orbEntry}) rotate(${spin}deg)`,
            borderRadius: "50%",
            background: `radial-gradient(circle, rgba(${i === 1 ? "0,255,255" : i === 0 ? "255,0,51" : "255,215,0"},0.3) 0%, transparent 70%)`,
            border: `3px solid ${glowColor}`,
            boxShadow: `0 0 40px ${glowColor}, 0 0 80px ${glowColor}44`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}>
            <div style={{
              transform: `rotate(${-spin}deg)`,
              textAlign: "center",
            }}>
              <div style={{
                fontSize: 42,
                fontWeight: 900,
                color: glowColor,
                fontFamily: "'Arial Black','Arial',sans-serif",
              }}>
                #{i + 1}
              </div>
              <div style={{
                fontSize: 16,
                color: "rgba(255,255,255,0.6)",
                fontFamily: "'Arial',sans-serif",
              }}>
                AD
              </div>
            </div>
          </div>
        );
      })}

      {/* Assembly line elements */}
      {["✍️ SCRIPT", "🎬 SHOT", "🎵 MUSIC", "🎙️ VO"].map((step, i) => {
        const delay = 25 + i * 10;
        const entryT = interpolate(f, [delay, delay + 20], [0, 1], {
          extrapolateLeft: "clamp", extrapolateRight: "clamp",
        });
        return (
          <div key={i} style={{
            position: "absolute",
            top: 1020 + i * 70,
            left: 80,
            right: 80,
            display: "flex",
            alignItems: "center",
            gap: 20,
            opacity: entryT,
            transform: `translateX(${(1 - entryT) * -80}px)`,
          }}>
            <div style={{
              fontSize: 22,
              fontWeight: 900,
              color: CYAN,
              fontFamily: "'Arial Black','Arial',sans-serif",
              letterSpacing: 2,
              minWidth: 160,
            }}>
              {step}
            </div>
            {/* Progress bar */}
            <div style={{ flex: 1, height: 6, background: "rgba(255,255,255,0.1)", borderRadius: 3 }}>
              <div style={{
                height: "100%",
                width: `${(entryT * 100)}%`,
                background: CYAN,
                borderRadius: 3,
                boxShadow: `0 0 8px ${CYAN}`,
              }} />
            </div>
            <div style={{ color: CYAN, fontSize: 18, fontFamily: "'Arial',sans-serif" }}>✓</div>
          </div>
        );
      })}

      {/* "7–12 DAYS" punch */}
      <div style={{
        position: "absolute",
        bottom: 100,
        left: "50%",
        transform: `translateX(-50%) scale(${daysScale})`,
        textAlign: "center",
        opacity: daysEntry,
      }}>
        <NeonText color={GOLD} style={{
          fontSize: 110,
          fontWeight: 900,
          fontFamily: "'Arial Black','Arial',sans-serif",
          letterSpacing: -2,
          lineHeight: 1,
        }}>
          7–12 DAYS
        </NeonText>
        <div style={{
          fontSize: 28,
          color: "rgba(255,255,255,0.5)",
          fontFamily: "'Arial',sans-serif",
          marginTop: 4,
        }}>
          turnaround
        </div>
      </div>

      <Scanlines />
      <Grain />
      <GlitchBars intensity={0.4} />
      <Vignette />
    </AbsoluteFill>
  );
};
