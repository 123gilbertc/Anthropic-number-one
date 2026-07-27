"use client";

import { create } from "zustand";
import type { BackgroundAsset } from "@anglecast/shared";

export interface RoomUiState {
  micEnabled: boolean;
  camEnabled: boolean;
  screenSharing: boolean;
  recording: boolean;
  recordingConsented: boolean;
  backgroundId: string;
  vbStatsFps: number;
  setMicEnabled: (v: boolean) => void;
  setCamEnabled: (v: boolean) => void;
  setScreenSharing: (v: boolean) => void;
  setRecording: (v: boolean) => void;
  setRecordingConsented: (v: boolean) => void;
  setBackgroundId: (id: string) => void;
  setVbStatsFps: (fps: number) => void;
}

export const useRoomStore = create<RoomUiState>((set) => ({
  micEnabled: true,
  camEnabled: true,
  screenSharing: false,
  recording: false,
  recordingConsented: false,
  backgroundId: "office",
  vbStatsFps: 0,
  setMicEnabled: (micEnabled) => set({ micEnabled }),
  setCamEnabled: (camEnabled) => set({ camEnabled }),
  setScreenSharing: (screenSharing) => set({ screenSharing }),
  setRecording: (recording) => set({ recording }),
  setRecordingConsented: (recordingConsented) => set({ recordingConsented }),
  setBackgroundId: (backgroundId) => set({ backgroundId }),
  setVbStatsFps: (vbStatsFps) => set({ vbStatsFps }),
}));

export const PRESET_BACKGROUNDS: BackgroundAsset[] = [
  { id: "none", kind: "none", label: "None (real room)" },
  { id: "blur-soft", kind: "color", label: "Soft charcoal", color: "#1A2330" },
  { id: "office", kind: "color", label: "Modern office", color: "#2A3544" },
  { id: "restaurant", kind: "color", label: "Warm restaurant", color: "#3A2A22" },
  { id: "mansion", kind: "color", label: "Bright loft", color: "#4A5562" },
  { id: "studio", kind: "color", label: "Podcast black", color: "#0A0A0C" },
];
