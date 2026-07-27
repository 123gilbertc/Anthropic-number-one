"use client";

import { PRODUCT } from "@anglecast/shared";
import Link from "next/link";
import { useEffect, useState } from "react";

interface SessionPayload {
  sessionId: string;
  roomName: string;
  displayName: string;
  tracks: Array<{
    participantId: string;
    trackKind: string;
    mimeType: string;
    startedAt: string;
    endedAt?: string;
  }>;
  message?: string;
}

export function SessionViewer({ sessionId }: { sessionId: string }) {
  const [data, setData] = useState<SessionPayload | null>(null);

  useEffect(() => {
    const raw = sessionStorage.getItem(`anglecast:session:${sessionId}`);
    if (raw) {
      try {
        setData(JSON.parse(raw) as SessionPayload);
      } catch {
        setData(null);
      }
    }
  }, [sessionId]);

  return (
    <main className="min-h-screen bg-ink-900 bg-studio-glow px-6 py-12">
      <div className="mx-auto max-w-3xl">
        <Link href="/" className="font-display text-xl text-mist-100">
          {PRODUCT.name}
        </Link>

        <h1 className="mt-10 font-display text-3xl text-mist-100 sm:text-4xl">
          Your multi-cam episode is rendering…
        </h1>
        <p className="mt-3 max-w-xl text-mist-300">
          Separate camera, mic, and screen tracks are ready. Transcript and AI angles hook in next —
          for now you can review what was captured locally.
        </p>

        {!data ? (
          <div className="mt-10 rounded-2xl border border-white/10 bg-ink-800/80 p-6 text-sm text-mist-500">
            No local session artifact found for <code className="text-mist-300">{sessionId}</code>.
            Record a session from the studio to populate this view.
          </div>
        ) : (
          <div className="mt-10 space-y-4">
            <div className="rounded-2xl bg-ink-800/90 p-5 shadow-panel">
              <p className="text-xs uppercase tracking-wider text-mist-500">Session</p>
              <p className="mt-1 text-mist-100">
                {data.roomName} · {data.displayName}
              </p>
              <p className="mt-2 text-sm text-mist-300">{data.message}</p>
            </div>

            <div className="rounded-2xl bg-ink-800/90 p-5 shadow-panel">
              <p className="text-xs uppercase tracking-wider text-mist-500">Tracks</p>
              <ul className="mt-3 divide-y divide-white/5">
                {data.tracks.map((t, i) => (
                  <li key={i} className="flex items-center justify-between py-3 text-sm">
                    <span className="text-mist-100 capitalize">{t.trackKind}</span>
                    <span className="text-mist-500">{t.mimeType}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="rounded-2xl border border-dashed border-brass-400/30 bg-brass-400/5 p-5">
              <p className="text-sm font-medium text-brass-300">Coming next</p>
              <p className="mt-1 text-sm text-mist-400">
                Speaker-diarized transcript · synthetic multi-angles · Podcast Layout auto-edit
              </p>
            </div>
          </div>
        )}

        <Link
          href="/"
          className="mt-10 inline-flex rounded-xl bg-brass-400 px-5 py-3 text-sm font-semibold text-ink-950"
        >
          Back to studio lobby
        </Link>
      </div>
    </main>
  );
}
