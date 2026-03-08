import { useParams, useSearchParams } from 'react-router-dom'
import { VesselCard } from '../embed/VesselCard'

export function EmbedVesselPage() {
  const { vesselId } = useParams()
  const [searchParams] = useSearchParams()
  const apiUrl = searchParams.get('api') || window.location.origin

  return (
    <div style={{ padding: 16, background: 'transparent', minHeight: '100vh', display: 'flex', alignItems: 'start', justifyContent: 'center' }}>
      <VesselCard vesselId={Number(vesselId)} apiUrl={apiUrl} />
    </div>
  )
}
