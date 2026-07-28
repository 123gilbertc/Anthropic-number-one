import { AccessToken } from "livekit-server-sdk";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

export async function GET(req: NextRequest) {
  const room = req.nextUrl.searchParams.get("room");
  const identity = req.nextUrl.searchParams.get("identity") || "guest";
  const name = req.nextUrl.searchParams.get("name") || identity;

  if (!room) {
    return NextResponse.json({ error: "Missing room" }, { status: 400 });
  }

  const apiKey = process.env.LIVEKIT_API_KEY;
  const apiSecret = process.env.LIVEKIT_API_SECRET;
  const wsUrl = process.env.NEXT_PUBLIC_LIVEKIT_URL || process.env.LIVEKIT_URL;

  if (!apiKey || !apiSecret || !wsUrl) {
    return NextResponse.json(
      {
        error:
          "LiveKit is not configured. Set LIVEKIT_API_KEY, LIVEKIT_API_SECRET, and NEXT_PUBLIC_LIVEKIT_URL.",
        demo: true,
      },
      { status: 503 },
    );
  }

  const at = new AccessToken(apiKey, apiSecret, {
    identity: `${identity}-${crypto.randomUUID().slice(0, 6)}`,
    name,
    ttl: "2h",
  });
  at.addGrant({
    roomJoin: true,
    room,
    canPublish: true,
    canSubscribe: true,
    canPublishData: true,
  });

  const token = await at.toJwt();
  return NextResponse.json({ token, url: wsUrl, room });
}
