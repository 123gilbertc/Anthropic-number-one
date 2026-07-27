import { QUALITY_PRESETS, type QualityPreset, type VirtualBackgroundQuality } from "@anglecast/shared";
import type { ProcessorStats } from "./types";

/**
 * Drops quality when FPS dips below budget; recovers when stable.
 * Keeps virtual backgrounds ≥ ~24fps on mid-range laptops.
 */
export class AdaptiveQualityController {
  private preset: QualityPreset;
  private samples: number[] = [];
  private readonly windowSize = 45;
  private lastChange = 0;

  constructor(initial: QualityPreset = "balanced") {
    this.preset = initial;
  }

  get quality(): VirtualBackgroundQuality {
    return QUALITY_PRESETS[this.preset];
  }

  get presetName(): QualityPreset {
    return this.preset;
  }

  recordFrame(frameMs: number): ProcessorStats {
    const fps = frameMs > 0 ? 1000 / frameMs : 0;
    this.samples.push(fps);
    if (this.samples.length > this.windowSize) this.samples.shift();

    const now = performance.now();
    if (now - this.lastChange > 2000 && this.samples.length >= 15) {
      const avg = this.samples.reduce((a, b) => a + b, 0) / this.samples.length;
      const target = this.quality.targetFps;
      if (avg < target * 0.85) {
        this.downgrade();
        this.lastChange = now;
      } else if (avg > target * 1.15) {
        this.upgrade();
        this.lastChange = now;
      }
    }

    return {
      fps,
      lastSegmentMs: frameMs,
      quality: this.preset,
    };
  }

  private downgrade() {
    if (this.preset === "high") this.preset = "balanced";
    else if (this.preset === "balanced") this.preset = "low";
  }

  private upgrade() {
    if (this.preset === "low") this.preset = "balanced";
    else if (this.preset === "balanced") this.preset = "high";
  }

  setPreset(preset: QualityPreset) {
    this.preset = preset;
    this.samples = [];
  }
}
