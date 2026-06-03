import "./index.css";
import { Composition } from "remotion";
import { AdVideo, TOTAL_DURATION } from "./AdVideo";
import { RoofVideo, ROOF_COMPOSITION_DURATION } from "./RoofVideo";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="CyberAd"
        component={AdVideo}
        fps={30}
        width={1080}
        height={1920}
        durationInFrames={TOTAL_DURATION}
        defaultProps={{}}
      />
      <Composition
        id="RoofVideo"
        component={RoofVideo}
        fps={30}
        width={1080}
        height={1920}
        durationInFrames={ROOF_COMPOSITION_DURATION}
        defaultProps={{}}
      />
    </>
  );
};
