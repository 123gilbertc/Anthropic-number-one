"use client";

import type { BackgroundAsset } from "@anglecast/shared";
import clsx from "clsx";
import { useMemo } from "react";
import { PRESET_BACKGROUNDS, useRoomStore } from "@/lib/room-store";

export function BackgroundPicker({ disabled }: { disabled?: boolean }) {
  const backgroundId = useRoomStore((s) => s.backgroundId);
  const setBackgroundId = useRoomStore((s) => s.setBackgroundId);
  const addCustomBackground = useRoomStore((s) => s.addCustomBackground);
  const customBackgrounds = useRoomStore((s) => s.customBackgrounds);
  const fps = useRoomStore((s) => s.vbStatsFps);

  const items = useMemo(
    () => [...PRESET_BACKGROUNDS, ...customBackgrounds],
    [customBackgrounds],
  );

  return (
    <div className="rounded-2xl bg-ink-800/90 p-4 shadow-panel">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-mist-100">Virtual set</h2>
        {fps > 0 && (
          <span className="text-[11px] text-mist-500">{fps} fps mask</span>
        )}
      </div>
      <p className="mt-1 text-xs text-mist-500">
        Swap live — cinematic sets stay behind you, on-device only.
      </p>
      <ul className="mt-4 grid grid-cols-2 gap-2">
        {items.map((bg) => (
          <li key={bg.id}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => setBackgroundId(bg.id)}
              className={clsx(
                "group flex w-full flex-col overflow-hidden rounded-xl border text-left transition disabled:opacity-40",
                backgroundId === bg.id
                  ? "border-brass-400 ring-1 ring-brass-400/40"
                  : "border-white/10 hover:border-white/25",
              )}
            >
              <span
                className="relative h-16 w-full overflow-hidden bg-ink-900 bg-cover bg-center"
                style={thumbStyle(bg)}
              >
                <span className="absolute inset-0 bg-gradient-to-t from-ink-950/50 to-transparent opacity-80 transition group-hover:opacity-50" />
                {bg.kind === "none" && (
                  <span className="absolute inset-0 flex items-center justify-center text-[10px] uppercase tracking-wider text-mist-500">
                    Camera
                  </span>
                )}
              </span>
              <span className="px-2 py-2 text-[11px] text-mist-300">{bg.label}</span>
            </button>
          </li>
        ))}
      </ul>
      <label className="mt-4 block cursor-pointer rounded-xl border border-dashed border-white/15 px-3 py-3 text-center text-xs text-mist-500 hover:border-brass-400/40 hover:text-mist-300">
        Upload custom image
        <input
          type="file"
          accept="image/*"
          className="hidden"
          disabled={disabled}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            const url = URL.createObjectURL(file);
            addCustomBackground({
              id: `custom-${Date.now()}`,
              kind: "image",
              label: file.name.slice(0, 18),
              src: url,
              thumbnail: url,
            });
          }}
        />
      </label>
    </div>
  );
}

function thumbStyle(bg: BackgroundAsset): React.CSSProperties {
  if ((bg.kind === "image" || bg.kind === "video") && (bg.thumbnail || bg.src)) {
    return { backgroundImage: `url(${bg.thumbnail || bg.src})` };
  }
  if (bg.kind === "color" && bg.color) {
    return { background: bg.color };
  }
  return {
    background: "linear-gradient(135deg, #1a2330 0%, #0b0f14 55%, #243041 100%)",
  };
}
