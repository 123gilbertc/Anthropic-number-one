import type { RecordableTrackKind, TrackRecordingMeta } from "@anglecast/shared";

export interface UploadTarget {
  /** Presigned PUT URL or custom upload endpoint */
  upload: (chunk: Blob, meta: { index: number; trackId: string }) => Promise<void>;
  /** Called when recording finishes to finalize multipart upload */
  complete?: (trackId: string, parts: number) => Promise<{ objectKey: string }>;
}

export interface LocalRecorderOptions {
  participantId: string;
  trackKind: RecordableTrackKind;
  mimeType?: string;
  /** Chunk interval for progressive upload (ms) */
  timesliceMs?: number;
  upload?: UploadTarget;
  onChunk?: (blob: Blob, index: number) => void;
}

/**
 * Riverside-style local multi-track recorder.
 * Captures high-quality local MediaStreamTracks and optionally progressive-uploads chunks.
 */
export class LocalTrackRecorder {
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private chunkIndex = 0;
  private startedAt = "";
  readonly trackId: string;
  private readonly opts: LocalRecorderOptions;

  constructor(opts: LocalRecorderOptions) {
    this.opts = opts;
    this.trackId = `${opts.participantId}-${opts.trackKind}-${crypto.randomUUID().slice(0, 8)}`;
  }

  start(stream: MediaStream) {
    const mimeType =
      this.opts.mimeType ??
      pickMimeType([
        "video/webm;codecs=vp9,opus",
        "video/webm;codecs=vp8,opus",
        "video/webm",
        "audio/webm;codecs=opus",
        "audio/webm",
      ]);

    this.chunks = [];
    this.chunkIndex = 0;
    this.startedAt = new Date().toISOString();
    this.recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);

    this.recorder.ondataavailable = async (ev) => {
      if (!ev.data.size) return;
      const index = this.chunkIndex++;
      this.chunks.push(ev.data);
      this.opts.onChunk?.(ev.data, index);
      await this.opts.upload?.upload(ev.data, { index, trackId: this.trackId });
    };

    this.recorder.start(this.opts.timesliceMs ?? 2000);
  }

  async stop(): Promise<{ blob: Blob; meta: TrackRecordingMeta }> {
    const recorder = this.recorder;
    if (!recorder) {
      throw new Error("Recorder was never started");
    }

    await new Promise<void>((resolve) => {
      recorder.onstop = () => resolve();
      if (recorder.state !== "inactive") recorder.stop();
      else resolve();
    });

    const mimeType = recorder.mimeType || "application/octet-stream";
    const blob = new Blob(this.chunks, { type: mimeType });
    let objectKey: string | undefined;
    if (this.opts.upload?.complete) {
      const res = await this.opts.upload.complete(this.trackId, this.chunks.length);
      objectKey = res.objectKey;
    }

    return {
      blob,
      meta: {
        participantId: this.opts.participantId,
        trackKind: this.opts.trackKind,
        mimeType,
        startedAt: this.startedAt,
        endedAt: new Date().toISOString(),
        objectKey,
      },
    };
  }
}

function pickMimeType(candidates: string[]): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  return candidates.find((t) => MediaRecorder.isTypeSupported(t));
}
