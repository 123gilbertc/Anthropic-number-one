"use client";

export function RecordingConsentModal({
  onAccept,
  onDecline,
}: {
  onAccept: () => void;
  onDecline: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/80 p-6 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-2xl bg-ink-800 p-6 shadow-panel">
        <h2 className="font-display text-xl text-mist-100">Start recording?</h2>
        <p className="mt-3 text-sm leading-relaxed text-mist-300">
          AngleCast records your camera, microphone, and screen locally at full quality, then uploads
          in the background. A live <span className="text-signal-live">REC</span> indicator stays
          visible for everyone — we never record silently.
        </p>
        <p className="mt-2 text-xs text-mist-500">
          By continuing you confirm all participants have agreed to be recorded.
        </p>
        <div className="mt-6 flex gap-3">
          <button
            type="button"
            onClick={onDecline}
            className="flex-1 rounded-xl bg-ink-700 px-4 py-3 text-sm text-mist-300"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onAccept}
            className="flex-1 rounded-xl bg-brass-400 px-4 py-3 text-sm font-semibold text-ink-950"
          >
            I consent — record
          </button>
        </div>
      </div>
    </div>
  );
}
