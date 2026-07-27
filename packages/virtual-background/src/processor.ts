import type { QualityPreset } from "@anglecast/shared";
import { AdaptiveQualityController } from "./adaptive";
import { WebGLCompositor, type BackgroundSourceLike } from "./compositor";
import { PersonSegmenter } from "./segmenter";
import type { BackgroundSource, ProcessorStats, VirtualBackgroundOptions } from "./types";

/**
 * Core pipeline: camera frame → MediaPipe mask → temporal smooth → WebGL composite.
 * Works with any MediaStreamTrack (LiveKit or raw getUserMedia).
 */
export class VirtualBackgroundProcessor {
  private segmenter: PersonSegmenter;
  private compositor: WebGLCompositor;
  private adaptive: AdaptiveQualityController;
  private background: BackgroundSource = { kind: "none" };
  private sourceVideo: HTMLVideoElement;
  private maskCanvas: HTMLCanvasElement;
  private maskCtx: CanvasRenderingContext2D;
  private raf = 0;
  private running = false;
  private outputStream: MediaStream | null = null;
  private onStats?: (stats: ProcessorStats) => void;
  private inputTrack: MediaStreamTrack | null = null;

  constructor(options: VirtualBackgroundOptions = {}) {
    this.segmenter = new PersonSegmenter({
      wasmPath: options.wasmPath,
      modelAssetPath: options.modelAssetPath,
    });
    this.compositor = new WebGLCompositor();
    this.adaptive = new AdaptiveQualityController(options.quality ?? "balanced");
    this.onStats = options.onStats;
    if (options.background) this.background = options.background;

    this.sourceVideo = document.createElement("video");
    this.sourceVideo.muted = true;
    this.sourceVideo.playsInline = true;
    this.sourceVideo.autoplay = true;

    this.maskCanvas = document.createElement("canvas");
    const ctx = this.maskCanvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) throw new Error("2D canvas unavailable");
    this.maskCtx = ctx;
  }

  get canvas(): HTMLCanvasElement {
    return this.compositor.canvas;
  }

  get processedTrack(): MediaStreamTrack | undefined {
    return this.outputStream?.getVideoTracks()[0];
  }

  async setBackground(background: BackgroundSource) {
    this.background = background;
  }

  setQuality(preset: QualityPreset) {
    this.adaptive.setPreset(preset);
  }

  async start(track: MediaStreamTrack): Promise<MediaStreamTrack> {
    await this.segmenter.ensureReady();
    this.inputTrack = track;
    const stream = new MediaStream([track]);
    this.sourceVideo.srcObject = stream;
    await this.sourceVideo.play();

    const settings = track.getSettings();
    const w = settings.width ?? 1280;
    const h = settings.height ?? 720;
    this.compositor.resize(w, h);

    const fps = settings.frameRate ?? 30;
    this.outputStream = this.compositor.canvas.captureStream(fps);
    const out = this.outputStream.getVideoTracks()[0];
    if (!out) throw new Error("Failed to capture processed stream");

    this.running = true;
    this.loop();
    return out;
  }

  private loop = () => {
    if (!this.running) return;
    const t0 = performance.now();
    this.processFrame();
    const stats = this.adaptive.recordFrame(performance.now() - t0);
    this.onStats?.(stats);
    this.raf = requestAnimationFrame(this.loop);
  };

  private processFrame() {
    const video = this.sourceVideo;
    if (video.readyState < 2) return;

    const q = this.adaptive.quality;
    this.maskCanvas.width = q.maskWidth;
    this.maskCanvas.height = q.maskHeight;
    this.maskCtx.drawImage(video, 0, 0, q.maskWidth, q.maskHeight);

    const segmented = this.segmenter.segment(this.maskCanvas, performance.now());
    if (!segmented) {
      // Passthrough while model warms up
      const ctx = this.compositor.canvas.getContext("2d");
      ctx?.drawImage(video, 0, 0, this.compositor.canvas.width, this.compositor.canvas.height);
      return;
    }

    const smoothed = this.compositor.smoothMask(
      segmented.data,
      segmented.width,
      segmented.height,
      q.temporalAlpha,
    );

    const bg = toCompositorBackground(this.background);
    this.compositor.draw({
      camera: video,
      mask: smoothed,
      maskWidth: segmented.width,
      maskHeight: segmented.height,
      background: bg,
    });
  }

  async stop() {
    this.running = false;
    cancelAnimationFrame(this.raf);
    this.sourceVideo.pause();
    this.sourceVideo.srcObject = null;
    this.outputStream?.getTracks().forEach((t) => t.stop());
    this.outputStream = null;
    this.inputTrack = null;
  }

  async destroy() {
    await this.stop();
    this.segmenter.destroy();
    this.compositor.destroy();
  }
}

function toCompositorBackground(bg: BackgroundSource): BackgroundSourceLike {
  if (bg.kind === "none") return { kind: "none" };
  if (bg.kind === "color") return { kind: "color", color: bg.color };
  if (bg.kind === "image") return { kind: "image", element: bg.element };
  return { kind: "video", element: bg.element };
}
