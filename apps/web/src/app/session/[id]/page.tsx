import { SessionViewer } from "@/components/session/SessionViewer";

export default async function SessionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <SessionViewer sessionId={id} />;
}
