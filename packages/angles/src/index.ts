import {
  DEFAULT_ANGLE_TRAJECTORIES,
  type AngleLabel,
  type CameraTrajectory,
} from "@anglecast/shared";

export interface AngleGenerationRequest {
  /** Source camera recording (composited with virtual background) */
  sourceVideo: Blob | string;
  /** Background asset used during the session — strong conditioning signal */
  backgroundAsset: { kind: "color" | "image" | "video"; src: string };
  cameraTrajectories?: CameraTrajectory[];
  /** Optional identity / session metadata for provider routing */
  sessionId?: string;
}

export interface GeneratedAngle {
  label: AngleLabel;
  /** URL or object key for the synthetic angle video */
  url: string;
  trajectory: CameraTrajectory;
  provider: string;
}

/**
 * Pluggable multi-angle generation — swap Stability / Kling / Seedance without
 * touching the editor. Phase 1 ships a stub; Phase 2 wires real providers.
 */
export interface AngleGenerator {
  readonly id: string;
  generateAngles(req: AngleGenerationRequest): Promise<GeneratedAngle[]>;
}

/** No-op generator used until a cloud multi-view provider is configured */
export class StubAngleGenerator implements AngleGenerator {
  readonly id = "stub";

  async generateAngles(req: AngleGenerationRequest): Promise<GeneratedAngle[]> {
    const trajectories = req.cameraTrajectories ?? DEFAULT_ANGLE_TRAJECTORIES;
    return trajectories.map((trajectory) => ({
      label: trajectory.label,
      url: typeof req.sourceVideo === "string" ? req.sourceVideo : "",
      trajectory,
      provider: this.id,
    }));
  }
}

let active: AngleGenerator = new StubAngleGenerator();

export function setAngleGenerator(generator: AngleGenerator) {
  active = generator;
}

export function getAngleGenerator(): AngleGenerator {
  return active;
}

export async function generateAngles(
  req: AngleGenerationRequest,
): Promise<GeneratedAngle[]> {
  return getAngleGenerator().generateAngles(req);
}

export { DEFAULT_ANGLE_TRAJECTORIES };
export type { CameraTrajectory, AngleLabel };
