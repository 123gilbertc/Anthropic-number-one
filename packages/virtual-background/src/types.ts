export type { BackgroundAsset, QualityPreset, VirtualBackgroundQuality } from "@anglecast/shared";

export type BackgroundSource =
  | { kind: "none" }
  | { kind: "color"; color: string }
  | { kind: "image"; element: HTMLImageElement | HTMLCanvasElement | ImageBitmap }
  | { kind: "video"; element: HTMLVideoElement };

export interface ProcessorStats {
  fps: number;
  lastSegmentMs: number;
  quality: "high" | "balanced" | "low";
}

export interface VirtualBackgroundOptions {
  /** Initial background */
  background?: BackgroundSource;
  /** Quality preset or custom */
  quality?: "high" | "balanced" | "low";
  /** CDN base for MediaPipe WASM + model assets */
  wasmPath?: string;
  modelAssetPath?: string;
  /** Called when adaptive quality changes */
  onStats?: (stats: ProcessorStats) => void;
}
