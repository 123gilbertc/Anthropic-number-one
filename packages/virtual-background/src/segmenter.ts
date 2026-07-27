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
 * Segmentation runs at a reduced resolution for speed; compositor upsamples the mask.
 */
export class PersonSegmenter {
  private segmenter: ImageSegmenter | null = null;
  private ready: Promise<void>;
  private lastTimestamp = -1;

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
      outputCategoryMask: true,
      outputConfidenceMasks: false,
    });
  }

  async ensureReady() {
    await this.ready;
  }

  /**
   * Returns category mask bytes (person ≈ 1 / background ≈ 0) plus dimensions.
   * MediaPipe selfie models encode foreground as category index; we normalize to 0–255 alpha.
   */
  segment(frame: HTMLVideoElement | HTMLCanvasElement | ImageBitmap, timestampMs: number): {
    data: Uint8Array;
    width: number;
    height: number;
  } | null {
    if (!this.segmenter) return null;
    // MediaPipe requires strictly increasing timestamps in VIDEO mode
    const ts = timestampMs <= this.lastTimestamp ? this.lastTimestamp + 1 : timestampMs;
    this.lastTimestamp = ts;

    let result: ImageSegmenterResult | undefined;
    this.segmenter.segmentForVideo(frame, ts, (r) => {
      result = r;
    });
    if (!result?.categoryMask) return null;

    const mask = result.categoryMask;
    const width = mask.width;
    const height = mask.height;
    const raw = mask.getAsUint8Array();
    // Convert category indices → alpha (person categories are non-zero)
    const data = new Uint8Array(width * height);
    for (let i = 0; i < data.length; i++) {
      data[i] = raw[i]! > 0 ? 255 : 0;
    }
    mask.close();
    return { data, width, height };
  }

  destroy() {
    this.segmenter?.close();
    this.segmenter = null;
  }
}
