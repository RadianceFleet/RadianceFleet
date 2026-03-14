import { useParams } from "react-router-dom";
import { VesselTimeline } from "../components/VesselTimeline";

export function VesselTimelinePage() {
  const { id } = useParams<{ id: string }>();
  if (!id) return <p style={{ color: "var(--text-muted)" }}>Invalid vessel ID</p>;
  return <VesselTimeline vesselId={id} />;
}
