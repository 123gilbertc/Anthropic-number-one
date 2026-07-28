"use client";

import { useEffect, useMemo, useState } from "react";
import { RoomClient } from "@/components/room/RoomClient";
import { DemoStudio } from "@/components/room/DemoStudio";

interface TokenResponse {
  token?: string;
  url?: string;
  error?: string;
  demo?: boolean;
}

export function RoomGate({
  roomName,
  displayName,
}: {
  roomName: string;
  displayName: string;
}) {
  const [tokenData, setTokenData] = useState<TokenResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const q = new URLSearchParams({
          room: roomName,
          name: displayName,
          identity: displayName.toLowerCase().replace(/\s+/g, "-"),
        });
        const res = await fetch(`/api/livekit/token?${q.toString()}`);
        const data = (await res.json()) as TokenResponse;
        if (cancelled) return;
        if (!res.ok) {
          if (data.demo) setTokenData(data);
          else setError(data.error || "Could not join room");
        } else {
          setTokenData(data);
        }
      } catch {
        if (!cancelled) setError("Network error fetching LiveKit token");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [roomName, displayName]);

  const mode = useMemo(() => {
    if (tokenData?.token && tokenData.url) return "livekit" as const;
    if (tokenData?.demo) return "demo" as const;
    return null;
  }, [tokenData]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-ink-900 text-mist-300">
        Warming up the studio…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-ink-900 px-6 text-center">
        <p className="text-lg text-mist-100">Couldn&apos;t open the room</p>
        <p className="max-w-md text-sm text-mist-500">{error}</p>
      </div>
    );
  }

  if (mode === "demo") {
    return <DemoStudio roomName={roomName} displayName={displayName} />;
  }

  if (mode === "livekit" && tokenData?.token && tokenData.url) {
    return (
      <RoomClient
        roomName={roomName}
        displayName={displayName}
        token={tokenData.token}
        serverUrl={tokenData.url}
      />
    );
  }

  return null;
}
