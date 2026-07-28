"use client";

import { PRODUCT } from "@anglecast/shared";
import clsx from "clsx";

export function StudioChrome({
  brand,
  roomName,
  displayName,
  connected,
  onToggleMic,
  onToggleCam,
  onToggleScreen,
  onToggleRecord,
  micOn,
  camOn,
  screenOn,
  recording,
}: {
  brand: string;
  roomName: string;
  displayName: string;
  connected: boolean;
  onToggleMic: () => void;
  onToggleCam: () => void;
  onToggleScreen: () => void;
  onToggleRecord: () => void;
  micOn: boolean;
  camOn: boolean;
  screenOn: boolean;
  recording: boolean;
}) {
  return (
    <>
      <header className="flex items-center justify-between border-b border-white/5 px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="font-display text-lg text-mist-100">{brand}</span>
          <span className="hidden text-mist-500 sm:inline">/</span>
          <span className="hidden text-sm text-mist-300 sm:inline">{roomName}</span>
        </div>
        <div className="flex items-center gap-3 text-sm">
          {recording && (
            <span className="flex items-center gap-2 rounded-full bg-signal-live/15 px-3 py-1 text-signal-live">
              <span className="h-2 w-2 animate-pulse rounded-full bg-signal-live" />
              REC
            </span>
          )}
          <span
            className={clsx(
              "rounded-full px-3 py-1 text-xs",
              connected ? "bg-signal-ok/15 text-signal-ok" : "bg-mist-500/20 text-mist-300",
            )}
          >
            {connected ? "Live" : "Connecting…"}
          </span>
          <span className="text-mist-500">{displayName}</span>
        </div>
      </header>

      <div className="fixed bottom-0 left-0 right-0 z-40 border-t border-white/5 bg-ink-900/95 px-4 py-4 backdrop-blur-md">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-center gap-2">
          <ControlButton active={micOn} onClick={onToggleMic} label={micOn ? "Mute" : "Unmute"} />
          <ControlButton active={camOn} onClick={onToggleCam} label={camOn ? "Stop cam" : "Start cam"} />
          <ControlButton
            active={screenOn}
            onClick={onToggleScreen}
            label={screenOn ? "Stop share" : "Share screen"}
          />
          <ControlButton
            active={recording}
            danger
            onClick={onToggleRecord}
            label={recording ? "Stop recording" : "Record"}
          />
        </div>
        <p className="mx-auto mt-2 max-w-xl text-center text-[11px] text-mist-500">
          {PRODUCT.subTagline} · Host + up to {PRODUCT.maxGuests} guests
        </p>
      </div>
    </>
  );
}

function ControlButton({
  label,
  onClick,
  active,
  danger,
}: {
  label: string;
  onClick: () => void;
  active?: boolean;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "rounded-xl px-4 py-2.5 text-sm font-medium transition",
        danger && active && "bg-signal-live text-white",
        danger && !active && "bg-ink-700 text-mist-100 hover:bg-ink-600",
        !danger && active && "bg-ink-700 text-mist-100",
        !danger && !active && "bg-ink-800 text-mist-500 hover:bg-ink-700",
      )}
    >
      {label}
    </button>
  );
}
