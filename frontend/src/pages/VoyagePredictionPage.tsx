import { Link, useParams } from 'react-router-dom'
import { useVesselDetail } from '../hooks/useVessels'
import { useVoyagePrediction } from '../hooks/useVoyagePrediction'
import { Card } from '../components/ui/Card'
import { Spinner } from '../components/ui/Spinner'
import { EmptyState } from '../components/ui/EmptyState'
import { MapContainer, TileLayer, Polyline, useMap } from 'react-leaflet'
import { useEffect } from 'react'
import type { LatLngExpression, LatLngBoundsExpression } from 'leaflet'

function FitBounds({ bounds }: { bounds: LatLngBoundsExpression }) {
  const map = useMap()
  useEffect(() => {
    map.fitBounds(bounds, { padding: [30, 30] })
  }, [map, bounds])
  return null
}

const sectionHead: React.CSSProperties = {
  margin: '0 0 12px',
  fontSize: 14,
  color: 'var(--text-muted)',
  textTransform: 'uppercase',
  letterSpacing: 1,
}

const labelCell: React.CSSProperties = {
  color: 'var(--text-dim)',
  width: 180,
  fontSize: 13,
  paddingRight: 12,
  paddingBottom: 8,
}

const valueCell: React.CSSProperties = { fontSize: 13, paddingBottom: 8 }

export function VoyagePredictionPage() {
  const { id } = useParams<{ id: string }>()
  const { data: vessel, isLoading: vesselLoading } = useVesselDetail(id)
  const { data: prediction, isLoading: predLoading, error: predError } = useVoyagePrediction(id)

  if (vesselLoading) return <Spinner text="Loading vessel..." />
  if (!vessel) {
    return (
      <p style={{ color: 'var(--score-critical)' }}>
        Vessel not found. <Link to="/vessels">Back to search</Link>
      </p>
    )
  }

  const loading = predLoading
  const unavailable = predError && !predLoading
  const hasPrediction = prediction && (prediction.predicted_route.length > 0 || prediction.actual_route.length > 0)

  const predictedPositions: LatLngExpression[] = prediction?.predicted_route.map(p => [p.lat, p.lon] as LatLngExpression) ?? []
  const actualPositions: LatLngExpression[] = prediction?.actual_route.map(p => [p.lat, p.lon] as LatLngExpression) ?? []
  const allPositions = [...predictedPositions, ...actualPositions]
  const bounds: LatLngBoundsExpression | undefined = allPositions.length >= 2 ? allPositions as LatLngBoundsExpression : undefined

  return (
    <div style={{ maxWidth: 1100 }}>
      <Link to={`/vessels/${id}`} style={{ fontSize: 13 }}>&larr; Back to vessel</Link>

      <h2 style={{ margin: '12px 0 4px', fontSize: 18 }}>
        Voyage Prediction: {vessel.name ?? 'Unknown'}
      </h2>
      <p style={{ color: 'var(--text-dim)', margin: '0 0 20px', fontSize: 13 }}>
        MMSI {vessel.mmsi ?? '?'} &middot; IMO {vessel.imo ?? '?'} &middot; {vessel.flag ?? '??'}
      </p>

      {loading && <Spinner text="Loading voyage prediction..." />}
      {unavailable && (
        <Card>
          <EmptyState
            title="Voyage prediction not available"
            description="The voyage prediction endpoint is not yet available. This feature requires the backend voyage predictor API (task 4A)."
          />
        </Card>
      )}

      {!loading && !unavailable && !hasPrediction && prediction === null && (
        <Card>
          <EmptyState
            title="No voyage prediction"
            description="No voyage prediction data available for this vessel."
          />
        </Card>
      )}

      {hasPrediction && (
        <>
          {/* Map */}
          <Card style={{ marginBottom: 16, padding: 0, overflow: 'hidden' }}>
            <div style={{ height: 450 }}>
              <MapContainer
                center={[0, 0]}
                zoom={3}
                style={{ width: '100%', height: '100%' }}
                scrollWheelZoom
              >
                <TileLayer
                  attribution='&copy; OpenStreetMap'
                  url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                />
                {bounds && <FitBounds bounds={bounds} />}
                {actualPositions.length >= 2 && (
                  <Polyline
                    positions={actualPositions}
                    pathOptions={{ color: '#60a5fa', weight: 3, opacity: 0.9 }}
                  />
                )}
                {predictedPositions.length >= 2 && (
                  <Polyline
                    positions={predictedPositions}
                    pathOptions={{ color: '#f97316', weight: 3, opacity: 0.8, dashArray: '10 6' }}
                  />
                )}
              </MapContainer>
            </div>
          </Card>

          {/* Legend */}
          <Card style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', gap: 24, fontSize: 13 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <div style={{ width: 24, height: 3, background: '#60a5fa' }} />
                Actual track
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <div style={{ width: 24, height: 3, background: '#f97316', borderTop: '2px dashed #f97316' }} />
                Predicted route
              </div>
            </div>
          </Card>

          {/* Details */}
          <Card>
            <h3 style={sectionHead}>Route Template Details</h3>
            <table><tbody>
              <tr>
                <td style={labelCell}>Template Name</td>
                <td style={valueCell}>{prediction!.template_name ?? '-'}</td>
              </tr>
              <tr>
                <td style={labelCell}>Predicted Destination</td>
                <td style={valueCell}>{prediction!.predicted_destination ?? '-'}</td>
              </tr>
              <tr>
                <td style={labelCell}>Similarity Score</td>
                <td style={valueCell}>
                  {prediction!.similarity_score != null
                    ? `${(prediction!.similarity_score * 100).toFixed(1)}%`
                    : '-'}
                </td>
              </tr>
              <tr>
                <td style={labelCell}>Deviation Score</td>
                <td style={valueCell}>
                  {prediction!.deviation_score != null
                    ? <span style={{
                        color: prediction!.deviation_score > 50
                          ? 'var(--score-critical)'
                          : prediction!.deviation_score > 20
                            ? 'var(--score-medium)'
                            : 'var(--score-low)',
                        fontWeight: 600,
                      }}>
                        {prediction!.deviation_score.toFixed(1)}
                      </span>
                    : '-'}
                </td>
              </tr>
              <tr>
                <td style={labelCell}>Actual Track Points</td>
                <td style={valueCell}>{prediction!.actual_route.length}</td>
              </tr>
              <tr>
                <td style={labelCell}>Predicted Route Points</td>
                <td style={valueCell}>{prediction!.predicted_route.length}</td>
              </tr>
            </tbody></table>
          </Card>
        </>
      )}
    </div>
  )
}
