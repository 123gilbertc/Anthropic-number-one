/** Session / room identity */
export type RoomRole = "host" | "guest" | "producer";

export interface ParticipantIdentity {
  id: string;
  name: string;
  role: RoomRole;
}

/** Virtual background assets applied client-side only */
export type BackgroundKind = "none" | "color" | "image" | "video";

export interface BackgroundAsset {
  id: string;
  kind: BackgroundKind;
  label: string;
  /** Hex color for solid backgrounds */
  color?: string;
  /** Public URL or blob URL for image/video */
  src?: string;
  thumbnail?: string;
}

export interface VirtualBackgroundQuality {
  /** Segmentation input size (lower = faster) */
  maskWidth: number;
  maskHeight: number;
  /** Temporal EMA factor for mask smoothing (0–1) */
  temporalAlpha: number;
  /** Target composite FPS budget */
  targetFps: number;
}

export const QUALITY_PRESETS = {
  high: {
    maskWidth: 256,
    maskHeight: 256,
    temporalAlpha: 0.65,
    targetFps: 30,
  },
  balanced: {
    maskWidth: 192,
    maskHeight: 192,
    temporalAlpha: 0.7,
    targetFps: 24,
  },
  low: {
    maskWidth: 160,
    maskHeight: 160,
    temporalAlpha: 0.75,
    targetFps: 20,
  },
} as const satisfies Record<string, VirtualBackgroundQuality>;

export type QualityPreset = keyof typeof QUALITY_PRESETS;

/** Per-participant track kinds we record separately (Riverside-style) */
export type RecordableTrackKind = "camera" | "microphone" | "screen";

export interface TrackRecordingMeta {
  participantId: string;
  trackKind: RecordableTrackKind;
  mimeType: string;
  startedAt: string;
  endedAt?: string;
  objectKey?: string;
}

export interface TranscriptSegment {
  id: string;
  speakerId: string;
  speakerLabel: string;
  text: string;
  startMs: number;
  endMs: number;
}

export interface SessionArtifact {
  sessionId: string;
  roomName: string;
  createdAt: string;
  tracks: TrackRecordingMeta[];
  transcript: TranscriptSegment[];
  compositeUrl?: string;
  backgroundIds: string[];
}

/** Camera trajectories for Phase-2 synthetic multi-cam */
export type AngleLabel = "original" | "slight-left" | "slight-right" | "wider" | "closer";

export interface CameraTrajectory {
  label: AngleLabel;
  /** Degrees yaw around subject */
  yawDeg: number;
  /** Degrees pitch */
  pitchDeg: number;
  /** Zoom factor relative to original framing (1 = same) */
  zoom: number;
}

export const DEFAULT_ANGLE_TRAJECTORIES: CameraTrajectory[] = [
  { label: "slight-left", yawDeg: -12, pitchDeg: 0, zoom: 1 },
  { label: "slight-right", yawDeg: 12, pitchDeg: 0, zoom: 1 },
  { label: "wider", yawDeg: 0, pitchDeg: 2, zoom: 0.85 },
];

export const PRODUCT = {
  name: "AngleCast",
  tagline: "Record like Google Meet. Walk away with multi-cam podcast footage.",
  subTagline: "One camera. Infinite angles. Perfect backgrounds.",
  maxGuests: 8,
} as const;
