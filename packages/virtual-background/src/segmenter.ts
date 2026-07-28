import {
  FilesetResolver,
  ImageSegmenter,
  type ImageSegmenterResult,
} from "@mediapipe/tasks-vision";

const DEFAULT_WASM =
  "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.18/wasm";
/** Landscape selfie segmentation — faster, Meet-like quality for webcam framing */
const DEFAULT_MODEL =
  "https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter_landscape/float16/latest/selfie_segmenter_landscape.tflite";

export interface SegmenterConfig {
  wasmPath?: string;
  modelAssetPath?: string;
}

/**
 * Thin wrapper around MediaPipe Image Segmenter (Selfie Segmentation landscape).
 * Uses person confidence masks (not hard categories) so alpha is hair-friendly.
 * Auto-detects mask polarity — some builds encode person as 0 vs 1.
 */
export class PersonSegmenter {
  private segmenter: ImageSegmenter | null = null;
  private ready: Promise<void>;
  private lastTimestamp = -1;
  /** 1 = high confidence is person; -1 = inverted (high = background) */
  private polarity: 1 | -1 | 0 = 0;
  private polaritySamples = 0;

  constructor(config: SegmenterConfig = {}) {
    this.ready = this.init(config);
  }

  private async init(config: SegmenterConfig) {
    const vision = await FilesetResolver.forVisionTasks(config.wasmPath ?? DEFAULT_WASM);
    this.segmenter = await ImageSegmenter.createFromOptions(vision, {
      baseOptions: {
        modelAssetPath: config.modelAssetPath ?? DEFAULT_MODEL,
        delegate: "GPU",
      },
      runningMode: "VIDEO",
      // Confidence masks give soft person probability — required for stable edges
      outputCategoryMask: true,
      outputConfidenceMasks: true,
    });
  }

  async ensureReady() {
    await this.ready;
  }

  /**
   * Returns person alpha 0–255 (255 = keep camera / person, 0 = show virtual background).
   */
  segment(
    frame: HTMLVideoElement | HTMLCanvasElement | ImageBitmap,
    timestampMs: number,
  ): {
    data: Uint8Array;
    width: number;
    height: number;
  } | null {
    if (!this.segmenter) return null;
    const ts = timestampMs <= this.lastTimestamp ? this.lastTimestamp + 1 : timestampMs;
    this.lastTimestamp = ts;

    let result: ImageSegmenterResult | undefined;
    this.segmenter.segmentForVideo(frame, ts, (r) => {
      result = r;
    });
    if (!result) return null;

    const fromConfidence = this.fromConfidenceMasks(result);
    if (fromConfidence) {
      this.learnPolarity(fromConfidence.data, fromConfidence.width, fromConfidence.height);
      return this.applyPolarity(fromConfidence);
    }

    const fromCategory = this.fromCategoryMask(result);
    if (!fromCategory) return null;
    this.learnPolarity(fromCategory.data, fromCategory.width, fromCategory.height);
    return this.applyPolarity(fromCategory);
  }

  private fromConfidenceMasks(result: ImageSegmenterResult): {
    data: Uint8Array;
    width: number;
    height: number;
  } | null {
    const masks = result.confidenceMasks;
    if (!masks?.length) return null;

    // Selfie models: usually a single person confidence mask.
    // If multiple, pick the mask whose center mean is highest (person sits mid-frame).
    let best = masks[0]!;
    let bestCenter = -1;
    for (const m of masks) {
      const f = m.getAsFloat32Array();
      const center = meanCenter(f, m.width, m.height);
      if (center > bestCenter) {
        bestCenter = center;
        best = m;
      }
    }

    const width = best.width;
    const height = best.height;
    const floats = best.getAsFloat32Array();
    const data = new Uint8Array(width * height);
    for (let i = 0; i < data.length; i++) {
      const v = floats[i] ?? 0;
      data[i] = Math.round(Math.min(1, Math.max(0, v)) * 255);
    }
    for (const m of masks) m.close();
    result.categoryMask?.close();
    return { data, width, height };
  }

  private fromCategoryMask(result: ImageSegmenterResult): {
    data: Uint8Array;
    width: number;
    height: number;
  } | null {
    const mask = result.categoryMask;
    if (!mask) return null;
    const width = mask.width;
    const height = mask.height;
    const raw = mask.getAsUint8Array();
    const data = new Uint8Array(width * height);
    // Category models vary: treat non-zero as "label A". Polarity detection fixes person vs BG.
    for (let i = 0; i < data.length; i++) {
      const v = raw[i] ?? 0;
      // Support both 0/1 labels and 0/255 masks
      data[i] = v > 0 ? (v >= 128 ? v : 255) : 0;
    }
    mask.close();
    return { data, width, height };
  }

  /**
   * Webcam subjects are near frame center; background dominates the border.
   * If border > center, the mask is inverted relative to "high = person".
   */
  private learnPolarity(data: Uint8Array, width: number, height: number) {
    if (this.polarity !== 0 && this.polaritySamples >= 8) return;
    const center = meanRegion(data, width, height, 0.3, 0.7, 0.25, 0.75);
    const border = meanBorder(data, width, height, 0.12);
    this.polaritySamples += 1;
    // Need a clear separation before locking
    if (Math.abs(center - border) < 20) return;
    const guess: 1 | -1 = center >= border ? 1 : -1;
    if (this.polarity === 0) {
      this.polarity = guess;
    } else if (this.polarity !== guess) {
      // Majority vote while learning
      this.polarity = this.polaritySamples % 2 === 0 ? guess : this.polarity;
    }
  }

  private applyPolarity(mask: { data: Uint8Array; width: number; height: number }) {
    if (this.polarity !== -1) return mask;
    const data = new Uint8Array(mask.data.length);
    for (let i = 0; i < data.length; i++) {
      data[i] = 255 - mask.data[i]!;
    }
    return { data, width: mask.width, height: mask.height };
  }

  destroy() {
    this.segmenter?.close();
    this.segmenter = null;
  }
}

function meanCenter(floats: Float32Array, width: number, height: number): number {
  return meanRegionFloat(floats, width, height, 0.35, 0.65, 0.3, 0.7);
}

function meanRegionFloat(
  data: Float32Array,
  width: number,
  height: number,
  x0: number,
  x1: number,
  y0: number,
  y1: number,
): number {
  const left = Math.floor(width * x0);
  const right = Math.floor(width * x1);
  const top = Math.floor(height * y0);
  const bottom = Math.floor(height * y1);
  let sum = 0;
  let n = 0;
  for (let y = top; y < bottom; y++) {
    for (let x = left; x < right; x++) {
      sum += data[y * width + x] ?? 0;
      n++;
    }
  }
  return n ? sum / n : 0;
}

function meanRegion(
  data: Uint8Array,
  width: number,
  height: number,
  x0: number,
  x1: number,
  y0: number,
  y1: number,
): number {
  const left = Math.floor(width * x0);
  const right = Math.floor(width * x1);
  const top = Math.floor(height * y0);
  const bottom = Math.floor(height * y1);
  let sum = 0;
  let n = 0;
  for (let y = top; y < bottom; y++) {
    for (let x = left; x < right; x++) {
      sum += data[y * width + x] ?? 0;
      n++;
    }
  }
  return n ? sum / n : 0;
}

function meanBorder(data: Uint8Array, width: number, height: number, band: number): number {
  const bx = Math.max(1, Math.floor(width * band));
  const by = Math.max(1, Math.floor(height * band));
  let sum = 0;
  let n = 0;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (x < bx || x >= width - bx || y < by || y >= height - by) {
        sum += data[y * width + x] ?? 0;
        n++;
      }
    }
  }
  return n ? sum / n : 0;
}
