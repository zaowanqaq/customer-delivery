import "./index.css";
import { Composition } from "remotion";
import { XhsAutoShowVideo } from "./Composition";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="XhsAutoShowVideo"
        component={XhsAutoShowVideo}
        durationInFrames={540}
        fps={30}
        width={1080}
        height={1920}
      />
    </>
  );
};
