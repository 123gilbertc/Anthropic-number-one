export { VirtualBackgroundProcessor } from "./processor";
export { AngleCastBackgroundProcessor } from "./livekit-processor";
export { WebGLCompositor } from "./compositor";
export { PersonSegmenter } from "./segmenter";
export { AdaptiveQualityController } from "./adaptive";
export type {
  BackgroundSource,
  ProcessorStats,
  VirtualBackgroundOptions,
} from "./types";

/** Load an image URL into an HTMLImageElement ready for WebGL */
export function loadBackgroundImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`Failed to load background image: ${src}`));
    img.src = src;
  });
}

/** Load a looping muted video background */
export function loadBackgroundVideo(src: string): Promise<HTMLVideoElement> {
  return new Promise((resolve, reject) => {
    const video = document.createElement("video");
    video.crossOrigin = "anonymous";
    video.muted = true;
    video.loop = true;
    video.playsInline = true;
    video.onloadeddata = async () => {
      try {
        await video.play();
        resolve(video);
      } catch (e) {
        reject(e);
      }
    };
    video.onerror = () => reject(new Error(`Failed to load background video: ${src}`));
    video.src = src;
  });
}
