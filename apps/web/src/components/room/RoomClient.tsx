"use client";

import {
  AngleCastBackgroundProcessor,
  loadBackgroundImage,
  type BackgroundSource,
} from "@anglecast/virtual-background";
import { LocalTrackRecorder } from "@anglecast/recording";
import { PRODUCT } from "@anglecast/shared";
import {
  GridLayout,
  LiveKitRoom,
  ParticipantTile,
  RoomAudioRenderer,
  useLocalParticipant,
  useTracks,
  useRoomContext,
} from "@livekit/components-react";
import "@livekit/components-styles";
import { Track, type LocalVideoTrack } from "livekit-client";
import { useCallback, useEffect, useRef, useState } from "react";
import { resolveBackground, useRoomStore } from "@/lib/room-store";
import { StudioChrome } from "@/components/room/StudioChrome";
import { BackgroundPicker } from "@/components/room/BackgroundPicker";
import { RecordingConsentModal } from "@/components/room/RecordingConsentModal";

export function RoomClient({
  roomName,
  displayName,
  token,
  serverUrl,
}: {
  roomName: string;
  displayName: string;
  token: string;
  serverUrl: string;
}) {
  return (
    <LiveKitRoom
      token={token}
      serverUrl={serverUrl}
      connect
      video
      audio
      className="flex min-h-screen flex-col bg-ink-950"
      data-lk-theme="default"
    >
      <StudioSession roomName={roomName} displayName={displayName} />
      <RoomAudioRenderer />
    </LiveKitRoom>
  );
}

function StudioSession({ roomName, displayName }: { roomName: string; displayName: string }) {
  const room = useRoomContext();
  const { localParticipant } = useLocalParticipant();
  const tracks = useTracks(
    [
      { source: Track.Source.Camera, withPlaceholder: true },
      { source: Track.Source.ScreenShare, withPlaceholder: false },
    ],
    { onlySubscribed: false },
  );

  const backgroundId = useRoomStore((s) => s.backgroundId);
  const customBackgrounds = useRoomStore((s) => s.customBackgrounds);
  const recording = useRoomStore((s) => s.recording);
  const setRecording = useRoomStore((s) => s.setRecording);
  const setVbStatsFps = useRoomStore((s) => s.setVbStatsFps);
  const setScreenSharing = useRoomStore((s) => s.setScreenSharing);

  const processorRef = useRef<AngleCastBackgroundProcessor | null>(null);
  const recordersRef = useRef<LocalTrackRecorder[]>([]);
  const [processorReady, setProcessorReady] = useState(false);
  const [showConsent, setShowConsent] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  // Attach virtual background processor to local camera once
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const camPub = localParticipant.getTrackPublication(Track.Source.Camera);
        const track = camPub?.track as LocalVideoTrack | undefined;
        if (!track || processorRef.current) return;

        const processor = new AngleCastBackgroundProcessor({
          quality: "balanced",
          background: { kind: "color", color: "#0B0F14" },
          onStats: (s) => setVbStatsFps(Math.round(s.fps)),
        });
        await track.setProcessor(processor);
        if (cancelled) {
          await track.stopProcessor();
          return;
        }
        processorRef.current = processor;
        setProcessorReady(true);
      } catch (err) {
        console.error("Virtual background init failed", err);
        setToast("Virtual background unavailable on this device — using raw camera.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [localParticipant, setVbStatsFps]);

  // Live background switching
  useEffect(() => {
    const processor = processorRef.current;
    if (!processor || !processorReady) return;
    const asset = resolveBackground(backgroundId, customBackgrounds);
    if (!asset) return;

    let cancelled = false;
    (async () => {
      let source: BackgroundSource = { kind: "none" };
      if (asset.kind === "color" && asset.color) {
        source = { kind: "color", color: asset.color };
      } else if (asset.kind === "image" && asset.src) {
        const img = await loadBackgroundImage(asset.src);
        if (cancelled) return;
        source = { kind: "image", element: img };
      } else if (asset.kind === "none") {
        source = { kind: "none" };
      }
      await processor.setBackground(source);
    })();

    return () => {
      cancelled = true;
    };
  }, [backgroundId, customBackgrounds, processorReady]);

  const toggleScreenShare = useCallback(async () => {
    const enabled = localParticipant.isScreenShareEnabled;
    await localParticipant.setScreenShareEnabled(!enabled, {
      audio: true,
      // Prefer current tab / window via getDisplayMedia browser UI
    });
    setScreenSharing(!enabled);
  }, [localParticipant, setScreenSharing]);

  const beginRecording = useCallback(async () => {
    const pubs = [
      localParticipant.getTrackPublication(Track.Source.Camera),
      localParticipant.getTrackPublication(Track.Source.Microphone),
      localParticipant.getTrackPublication(Track.Source.ScreenShare),
    ];
    const recorders: LocalTrackRecorder[] = [];
    for (const pub of pubs) {
      if (!pub?.track) continue;
      const kind =
        pub.source === Track.Source.Camera
          ? "camera"
          : pub.source === Track.Source.Microphone
            ? "microphone"
            : "screen";
      const recorder = new LocalTrackRecorder({
        participantId: localParticipant.identity,
        trackKind: kind,
        timesliceMs: 2000,
      });
      recorder.start(new MediaStream([pub.track.mediaStreamTrack]));
      recorders.push(recorder);
    }
    recordersRef.current = recorders;
    setRecording(true);
    setToast("Local multi-track recording started. Progressive upload hooks are ready.");
  }, [localParticipant, setRecording]);

  const startRecording = useCallback(async () => {
    if (!useRoomStore.getState().recordingConsented) {
      setShowConsent(true);
      return;
    }
    await beginRecording();
  }, [beginRecording]);

  const stopRecording = useCallback(async () => {
    const results = await Promise.all(recordersRef.current.map((r) => r.stop()));
    recordersRef.current = [];
    setRecording(false);
    // Stash in sessionStorage for post-session viewer stub
    const sessionId = `sess_${Date.now()}`;
    sessionStorage.setItem(
      `anglecast:session:${sessionId}`,
      JSON.stringify({
        sessionId,
        roomName,
        displayName,
        tracks: results.map((r) => r.meta),
        message: "Your multi-cam episode tracks are captured locally.",
      }),
    );
    setToast("Recording saved. Opening session viewer…");
    window.location.href = `/session/${sessionId}`;
  }, [displayName, roomName, setRecording]);

  const screenTracks = tracks.filter((t) => t.source === Track.Source.ScreenShare);
  const cameraTracks = tracks.filter((t) => t.source === Track.Source.Camera);

  return (
    <>
      <StudioChrome
        brand={PRODUCT.name}
        roomName={roomName}
        displayName={displayName}
        connected={room.state === "connected"}
        onToggleMic={() =>
          localParticipant.setMicrophoneEnabled(!localParticipant.isMicrophoneEnabled)
        }
        onToggleCam={() =>
          localParticipant.setCameraEnabled(!localParticipant.isCameraEnabled)
        }
        onToggleScreen={toggleScreenShare}
        onToggleRecord={() => (recording ? stopRecording() : startRecording())}
        micOn={localParticipant.isMicrophoneEnabled}
        camOn={localParticipant.isCameraEnabled}
        screenOn={localParticipant.isScreenShareEnabled}
        recording={recording}
      />

      <div className="flex flex-1 flex-col gap-4 px-4 pb-28 pt-4 md:flex-row">
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          {screenTracks.length > 0 && (
            <div className="relative min-h-[40vh] flex-1 overflow-hidden rounded-2xl bg-ink-800 shadow-panel">
              <GridLayout tracks={screenTracks} className="h-full">
                <ParticipantTile />
              </GridLayout>
              <span className="absolute left-3 top-3 rounded-md bg-ink-950/70 px-2 py-1 text-xs text-mist-300">
                Desktop / screen
              </span>
            </div>
          )}
          <div
            className={`overflow-hidden rounded-2xl bg-ink-800 shadow-panel ${
              screenTracks.length ? "h-40 md:h-48" : "min-h-[50vh] flex-1"
            }`}
          >
            <GridLayout tracks={cameraTracks} className="h-full min-h-[240px]">
              <ParticipantTile />
            </GridLayout>
          </div>
        </div>

        <aside className="w-full shrink-0 md:w-72">
          <BackgroundPicker disabled={!processorReady} />
        </aside>
      </div>

      {showConsent && (
        <RecordingConsentModal
          onAccept={() => {
            useRoomStore.getState().setRecordingConsented(true);
            setShowConsent(false);
            void beginRecording();
          }}
          onDecline={() => setShowConsent(false)}
        />
      )}

      {toast && (
        <div className="fixed bottom-24 left-1/2 z-50 max-w-md -translate-x-1/2 rounded-xl bg-ink-700 px-4 py-3 text-center text-sm text-mist-100 shadow-panel">
          {toast}
          <button
            type="button"
            className="ml-3 text-brass-400"
            onClick={() => setToast(null)}
          >
            Dismiss
          </button>
        </div>
      )}
    </>
  );
}
