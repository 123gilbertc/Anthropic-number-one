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
  customBackgrounds: BackgroundAsset[];
  vbStatsFps: number;
  setMicEnabled: (v: boolean) => void;
  setCamEnabled: (v: boolean) => void;
  setScreenSharing: (v: boolean) => void;
  setRecording: (v: boolean) => void;
  setRecordingConsented: (v: boolean) => void;
  setBackgroundId: (id: string) => void;
  addCustomBackground: (asset: BackgroundAsset) => void;
  setVbStatsFps: (fps: number) => void;
}

/** Cinematic virtual sets — images live in /public/backgrounds */
export const PRESET_BACKGROUNDS: BackgroundAsset[] = [
  { id: "none", kind: "none", label: "None (real room)" },
  {
    id: "noir",
    kind: "image",
    label: "Neon noir",
    src: "/backgrounds/bg-noir.jpg",
    thumbnail: "/backgrounds/bg-noir.jpg",
  },
  {
    id: "office",
    kind: "image",
    label: "Daylit office",
    src: "/backgrounds/bg-office.jpg",
    thumbnail: "/backgrounds/bg-office.jpg",
  },
  {
    id: "restaurant",
    kind: "image",
    label: "Candle restaurant",
    src: "/backgrounds/bg-restaurant.jpg",
    thumbnail: "/backgrounds/bg-restaurant.jpg",
  },
  {
    id: "loft",
    kind: "image",
    label: "Sun loft",
    src: "/backgrounds/bg-loft.jpg",
    thumbnail: "/backgrounds/bg-loft.jpg",
  },
  {
    id: "studio",
    kind: "image",
    label: "Broadcast set",
    src: "/backgrounds/bg-studio.jpg",
    thumbnail: "/backgrounds/bg-studio.jpg",
  },
  { id: "ink", kind: "color", label: "Deep ink", color: "#0B0F14" },
  { id: "brass", kind: "color", label: "Warm brass wash", color: "#2A2118" },
];

export function resolveBackground(
  id: string,
  custom: BackgroundAsset[] = [],
): BackgroundAsset | undefined {
  return [...PRESET_BACKGROUNDS, ...custom].find((b) => b.id === id);
}

export const useRoomStore = create<RoomUiState>((set) => ({
  micEnabled: true,
  camEnabled: true,
  screenSharing: false,
  recording: false,
  recordingConsented: false,
  backgroundId: "noir",
  customBackgrounds: [],
  vbStatsFps: 0,
  setMicEnabled: (micEnabled) => set({ micEnabled }),
  setCamEnabled: (camEnabled) => set({ camEnabled }),
  setScreenSharing: (screenSharing) => set({ screenSharing }),
  setRecording: (recording) => set({ recording }),
  setRecordingConsented: (recordingConsented) => set({ recordingConsented }),
  setBackgroundId: (backgroundId) => set({ backgroundId }),
  addCustomBackground: (asset) =>
    set((s) => ({
      customBackgrounds: [...s.customBackgrounds, asset],
      backgroundId: asset.id,
    })),
  setVbStatsFps: (vbStatsFps) => set({ vbStatsFps }),
}));
