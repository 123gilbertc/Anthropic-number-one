"use client";

import {
  AngleCastBackgroundProcessor,
  loadBackgroundImage,
  type BackgroundSource,
} from "@anglecast/virtual-background";
import { LocalTrackRecorder } from "@anglecast/recording";
import { PRODUCT } from "@anglecast/shared";
import { useCallback, useEffect, useRef, useState } from "react";
import { resolveBackground, useRoomStore } from "@/lib/room-store";
import { StudioChrome } from "@/components/room/StudioChrome";
import { BackgroundPicker } from "@/components/room/BackgroundPicker";
import { RecordingConsentModal } from "@/components/room/RecordingConsentModal";

/**
 * Local-only studio when LiveKit credentials are not configured.
 * Still exercises camera, screen share, virtual backgrounds, and local recording.
 */
export function DemoStudio({
  roomName,
  displayName,
}: {
  roomName: string;
  displayName: string;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const screenRef = useRef<HTMLVideoElement>(null);
  const processorRef = useRef<AngleCastBackgroundProcessor | null>(null);
  const camStreamRef = useRef<MediaStream | null>(null);
  const screenStreamRef = useRef<MediaStream | null>(null);
  const recordersRef = useRef<LocalTrackRecorder[]>([]);

  const [micOn, setMicOn] = useState(true);
  const [camOn, setCamOn] = useState(true);
  const [screenOn, setScreenOn] = useState(false);
  const [ready, setReady] = useState(false);
  const [showConsent, setShowConsent] = useState(false);
  const [banner, setBanner] = useState(
    "Demo mode — add LiveKit keys for multi-party rooms. Virtual backgrounds still run fully local.",
  );

  const backgroundId = useRoomStore((s) => s.backgroundId);
  const customBackgrounds = useRoomStore((s) => s.customBackgrounds);
  const recording = useRoomStore((s) => s.recording);
  const setRecording = useRoomStore((s) => s.setRecording);
  const setVbStatsFps = useRoomStore((s) => s.setVbStatsFps);
  const setRecordingConsented = useRoomStore((s) => s.setRecordingConsented);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { width: 1280, height: 720, frameRate: 30 },
          audio: true,
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        camStreamRef.current = stream;
        const camTrack = stream.getVideoTracks()[0];
        if (!camTrack) throw new Error("No camera track");

        const processor = new AngleCastBackgroundProcessor({
          quality: "balanced",
          background: { kind: "color", color: "#0B0F14" },
          onStats: (s) => setVbStatsFps(Math.round(s.fps)),
        });
        await processor.init({ track: camTrack });
        processorRef.current = processor;

        if (videoRef.current && processor.processedTrack) {
          videoRef.current.srcObject = new MediaStream([processor.processedTrack]);
          await videoRef.current.play();
        }
        setReady(true);
      } catch (err) {
        console.error(err);
        setBanner(
          "Camera permission denied or unavailable. On mobile Safari, virtual backgrounds may be limited.",
        );
      }
    })();

    return () => {
      cancelled = true;
      void processorRef.current?.destroy();
      camStreamRef.current?.getTracks().forEach((t) => t.stop());
      screenStreamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, [setVbStatsFps]);

  useEffect(() => {
    const processor = processorRef.current;
    if (!processor || !ready) return;
    const asset = resolveBackground(backgroundId, customBackgrounds);
    if (!asset) return;
    let cancelled = false;
    (async () => {
      let source: BackgroundSource = { kind: "none" };
      if (asset.kind === "color" && asset.color) source = { kind: "color", color: asset.color };
      else if (asset.kind === "image" && asset.src) {
        const img = await loadBackgroundImage(asset.src);
        if (cancelled) return;
        source = { kind: "image", element: img };
      }
      await processor.setBackground(source);
    })();
    return () => {
      cancelled = true;
    };
  }, [backgroundId, customBackgrounds, ready]);

  const toggleMic = () => {
    const track = camStreamRef.current?.getAudioTracks()[0];
    if (track) {
      track.enabled = !track.enabled;
      setMicOn(track.enabled);
    }
  };

  const toggleCam = () => {
    const track = camStreamRef.current?.getVideoTracks()[0];
    if (track) {
      track.enabled = !track.enabled;
      setCamOn(track.enabled);
    }
  };

  const toggleScreen = async () => {
    if (screenOn) {
      screenStreamRef.current?.getTracks().forEach((t) => t.stop());
      screenStreamRef.current = null;
      if (screenRef.current) screenRef.current.srcObject = null;
      setScreenOn(false);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({
        video: true,
        audio: true,
      });
      screenStreamRef.current = stream;
      if (screenRef.current) {
        screenRef.current.srcObject = stream;
        await screenRef.current.play();
      }
      setScreenOn(true);
      stream.getVideoTracks()[0]?.addEventListener("ended", () => {
        setScreenOn(false);
        screenStreamRef.current = null;
      });
    } catch {
      setBanner("Screen share cancelled or unsupported in this browser.");
    }
  };

  const beginRecording = useCallback(async () => {
    const recorders: LocalTrackRecorder[] = [];
    const cam = camStreamRef.current;
    if (cam) {
      const videoTracks = processorRef.current?.processedTrack
        ? [processorRef.current.processedTrack]
        : cam.getVideoTracks();
      const audioTracks = cam.getAudioTracks();
      if (videoTracks[0]) {
        const r = new LocalTrackRecorder({
          participantId: displayName,
          trackKind: "camera",
        });
        r.start(new MediaStream([videoTracks[0]!]));
        recorders.push(r);
      }
      if (audioTracks[0]) {
        const r = new LocalTrackRecorder({
          participantId: displayName,
          trackKind: "microphone",
        });
        r.start(new MediaStream([audioTracks[0]!]));
        recorders.push(r);
      }
    }
    if (screenStreamRef.current) {
      const r = new LocalTrackRecorder({
        participantId: displayName,
        trackKind: "screen",
      });
      r.start(screenStreamRef.current);
      recorders.push(r);
    }
    recordersRef.current = recorders;
    setRecording(true);
  }, [displayName, setRecording]);

  const stopRecording = useCallback(async () => {
    const results = await Promise.all(recordersRef.current.map((r) => r.stop()));
    recordersRef.current = [];
    setRecording(false);
    const sessionId = `sess_${Date.now()}`;
    sessionStorage.setItem(
      `anglecast:session:${sessionId}`,
      JSON.stringify({
        sessionId,
        roomName,
        displayName,
        tracks: results.map((r) => r.meta),
        message: "Your multi-cam episode is ready to review.",
      }),
    );
    window.location.href = `/session/${sessionId}`;
  }, [displayName, roomName, setRecording]);

  return (
    <div className="flex min-h-screen flex-col bg-ink-950">
      <StudioChrome
        brand={PRODUCT.name}
        roomName={roomName}
        displayName={displayName}
        connected={ready}
        onToggleMic={toggleMic}
        onToggleCam={toggleCam}
        onToggleScreen={() => void toggleScreen()}
        onToggleRecord={() => {
          if (recording) void stopRecording();
          else if (!useRoomStore.getState().recordingConsented) setShowConsent(true);
          else void beginRecording();
        }}
        micOn={micOn}
        camOn={camOn}
        screenOn={screenOn}
        recording={recording}
      />

      <div className="mx-4 mt-3 rounded-xl border border-brass-400/20 bg-brass-400/10 px-4 py-2 text-xs text-brass-300">
        {banner}
      </div>

      <div className="flex flex-1 flex-col gap-4 px-4 pb-28 pt-4 md:flex-row">
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          {screenOn && (
            <div className="relative min-h-[36vh] flex-1 overflow-hidden rounded-2xl bg-ink-800 shadow-panel">
              <video ref={screenRef} className="h-full w-full object-contain" muted playsInline />
              <span className="absolute left-3 top-3 rounded-md bg-ink-950/70 px-2 py-1 text-xs text-mist-300">
                Desktop / screen
              </span>
            </div>
          )}
          <div
            className={`relative overflow-hidden rounded-2xl bg-ink-800 shadow-panel ${
              screenOn ? "h-44" : "min-h-[52vh] flex-1"
            }`}
          >
            <video
              ref={videoRef}
              className="h-full w-full object-cover"
              muted
              playsInline
              style={{ transform: "scaleX(-1)" }}
            />
            <span className="absolute bottom-3 left-3 rounded-md bg-ink-950/70 px-2 py-1 text-xs text-mist-300">
              {displayName} · you
            </span>
          </div>
        </div>
        <aside className="w-full shrink-0 md:w-72">
          <BackgroundPicker disabled={!ready} />
        </aside>
      </div>

      {showConsent && (
        <RecordingConsentModal
          onDecline={() => setShowConsent(false)}
          onAccept={() => {
            setRecordingConsented(true);
            setShowConsent(false);
            void beginRecording();
          }}
        />
      )}
    </div>
  );
}
