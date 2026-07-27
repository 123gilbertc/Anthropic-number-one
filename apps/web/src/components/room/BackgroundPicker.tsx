"use client";

import clsx from "clsx";
import { PRESET_BACKGROUNDS, useRoomStore } from "@/lib/room-store";

export function BackgroundPicker({ disabled }: { disabled?: boolean }) {
  const backgroundId = useRoomStore((s) => s.backgroundId);
  const setBackgroundId = useRoomStore((s) => s.setBackgroundId);
  const fps = useRoomStore((s) => s.vbStatsFps);

  return (
    <div className="rounded-2xl bg-ink-800/90 p-4 shadow-panel">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-mist-100">Virtual set</h2>
        {fps > 0 && (
          <span className="text-[11px] text-mist-500">{fps} fps mask</span>
        )}
      </div>
      <p className="mt-1 text-xs text-mist-500">
        Change live — applies only to your outbound camera. Segmentation stays on-device.
      </p>
      <ul className="mt-4 grid grid-cols-2 gap-2">
        {PRESET_BACKGROUNDS.map((bg) => (
          <li key={bg.id}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => setBackgroundId(bg.id)}
              className={clsx(
                "flex w-full flex-col overflow-hidden rounded-xl border text-left transition disabled:opacity-40",
                backgroundId === bg.id
                  ? "border-brass-400 ring-1 ring-brass-400/40"
                  : "border-white/10 hover:border-white/25",
              )}
            >
              <span
                className="h-14 w-full"
                style={{
                  background:
                    bg.kind === "color" && bg.color
                      ? bg.color
                      : "linear-gradient(135deg,#1a2330,#0b0f14)",
                }}
              />
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
            // Register a one-off preset dynamically via store background id convention
            PRESET_BACKGROUNDS.push({
              id: `custom-${Date.now()}`,
              kind: "image",
              label: file.name.slice(0, 18),
              src: url,
            });
            setBackgroundId(PRESET_BACKGROUNDS[PRESET_BACKGROUNDS.length - 1]!.id);
          }}
        />
      </label>
    </div>
  );
}
