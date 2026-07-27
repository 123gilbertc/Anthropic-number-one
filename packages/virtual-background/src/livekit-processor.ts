import type { Track, TrackProcessor } from "livekit-client";
import { VirtualBackgroundProcessor } from "./processor";
import type { BackgroundSource, ProcessorStats, VirtualBackgroundOptions } from "./types";

/**
 * LiveKit-compatible TrackProcessor wrapping AngleCast's MediaPipe + WebGL pipeline.
 * Applies only to the local participant's outbound camera track.
 */
export class AngleCastBackgroundProcessor implements TrackProcessor<Track.Kind.Video> {
  readonly name = "anglecast-virtual-background";
  processedTrack?: MediaStreamTrack;

  private inner: VirtualBackgroundProcessor;
  private sourceTrack?: MediaStreamTrack;

  constructor(options: VirtualBackgroundOptions = {}) {
    this.inner = new VirtualBackgroundProcessor(options);
  }

  async init(opts: { track: MediaStreamTrack; element?: HTMLMediaElement }) {
    this.sourceTrack = opts.track;
    this.processedTrack = await this.inner.start(opts.track);
  }

  async restart(opts: { track: MediaStreamTrack; element?: HTMLMediaElement }) {
    await this.inner.stop();
    this.sourceTrack = opts.track;
    this.processedTrack = await this.inner.start(opts.track);
  }

  async destroy() {
    await this.inner.destroy();
    this.processedTrack = undefined;
    this.sourceTrack = undefined;
  }

  async setBackground(background: BackgroundSource) {
    await this.inner.setBackground(background);
  }

  setQuality(preset: "high" | "balanced" | "low") {
    this.inner.setQuality(preset);
  }

  get canvas(): HTMLCanvasElement {
    return this.inner.canvas;
  }

  onStats(cb: (stats: ProcessorStats) => void) {
    // Re-bind via options is awkward mid-flight; expose canvas stats through a setter on inner
    (this.inner as unknown as { onStats?: (s: ProcessorStats) => void }).onStats = cb;
  }
}
