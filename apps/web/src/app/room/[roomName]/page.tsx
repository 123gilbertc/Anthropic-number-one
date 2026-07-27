import { RoomGate } from "@/components/room/RoomGate";

export default async function RoomPage({
  params,
  searchParams,
}: {
  params: Promise<{ roomName: string }>;
  searchParams: Promise<{ name?: string }>;
}) {
  const { roomName } = await params;
  const { name } = await searchParams;
  return (
    <RoomGate
      roomName={decodeURIComponent(roomName)}
      displayName={name?.trim() || "Guest"}
    />
  );
}
