import { useState, useEffect, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import FPRateByCorridorChart from "../components/charts/FPRateByCorridorChart";
import { RegionSelector } from "../components/RegionSelector";
import { SignalOverrideEditor } from "../components/SignalOverrideEditor";
import { ShadowScorePreview } from "../components/ShadowScorePreview";
import { CalibrationTimeline } from "../components/CalibrationTimeline";

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

interface CorridorFPRate {
  corridor_id: number;
  corridor_name: string;
  total_alerts: number;
  false_positives: number;
  fp_rate: number;
  fp_rate_30d: number;
  fp_rate_90d: number;
  trend: string;
}

interface CalibrationSuggestion {
  corridor_id: number;
  corridor_name: string;
  current_multiplier: number;
  suggested_multiplier: number;
  reason: string;
  fp_rate: number;
}

interface ScoringOverride {
  corridor_multiplier_override: number | null;
  gap_duration_multiplier: number;
  description: string;
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

function fpColor(rate: number): string {
  if (rate > 0.3) return "#ef4444";
  if (rate > 0.15) return "#f59e0b";
  if (rate < 0.05) return "#22c55e";
  return "#6b7280";
}

function fpBgClass(rate: number): string {
  if (rate > 0.3) return "bg-red-50";
  if (rate > 0.15) return "bg-yellow-50";
  if (rate < 0.05) return "bg-green-50";
  return "";
}

function trendArrow(trend: string): string {
  if (trend === "increasing") return " ↑";
  if (trend === "decreasing") return " ↓";
  return " →";
}

/* ------------------------------------------------------------------ */
/* Override Modal                                                      */
/* ------------------------------------------------------------------ */

function OverrideModal({
  corridorId,
  corridorName,
  onClose,
  onSave,
}: {
  corridorId: number;
  corridorName: string;
  onClose: () => void;
  onSave: (corridorId: number, body: ScoringOverride) => void;
}) {
  const [multiplier, setMultiplier] = useState<string>("");
  const [gapMultiplier, setGapMultiplier] = useState<string>("1.0");
  const [description, setDescription] = useState<string>("");

  const handleSave = useCallback(() => {
    onSave(corridorId, {
      corridor_multiplier_override: multiplier ? parseFloat(multiplier) : null,
      gap_duration_multiplier: parseFloat(gapMultiplier) || 1.0,
      description: description || "",
    });
  }, [corridorId, multiplier, gapMultiplier, description, onSave]);

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-md">
        <h3 className="text-lg font-semibold mb-4">
          Override: {corridorName}
        </h3>

        <label className="block text-sm font-medium mb-1">
          Corridor Multiplier Override
        </label>
        <input
          type="number"
          step="0.1"
          min="0.1"
          max="5.0"
          value={multiplier}
          onChange={(e) => setMultiplier(e.target.value)}
          placeholder="Leave empty to clear"
          className="w-full border rounded px-3 py-2 mb-3"
        />

        <label className="block text-sm font-medium mb-1">
          Gap Duration Multiplier
        </label>
        <input
          type="number"
          step="0.1"
          min="0.1"
          max="5.0"
          value={gapMultiplier}
          onChange={(e) => setGapMultiplier(e.target.value)}
          className="w-full border rounded px-3 py-2 mb-3"
        />

        <label className="block text-sm font-medium mb-1">Description</label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Reason for this override..."
          className="w-full border rounded px-3 py-2 mb-4 h-20"
        />

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 border rounded hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            Save Override
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Main Page                                                           */
/* ------------------------------------------------------------------ */

export default function FPTuningPage() {
  const queryClient = useQueryClient();
  const [selectedCorridor, setSelectedCorridor] = useState<{
    id: number;
    name: string;
  } | null>(null);
  const [dismissedSuggestions, setDismissedSuggestions] = useState<Set<number>>(
    () => new Set()
  );
  const [selectedRegion, setSelectedRegion] = useState<number | null>(null);
  const [signalOverrides, setSignalOverrides] = useState<Record<string, number>>({});

  const {
    data: fpRates,
    isLoading: ratesLoading,
    error: ratesError,
  } = useQuery<CorridorFPRate[]>({
    queryKey: ["fp-rates"],
    queryFn: () => apiFetch("/api/v1/corridors/fp-rates"),
  });

  const {
    data: suggestions,
    isLoading: suggestionsLoading,
  } = useQuery<CalibrationSuggestion[]>({
    queryKey: ["calibration-suggestions"],
    queryFn: () => apiFetch("/api/v1/corridors/calibration-suggestions"),
  });

  const overrideMutation = useMutation({
    mutationFn: ({
      corridorId,
      body,
    }: {
      corridorId: number;
      body: ScoringOverride;
    }) =>
      apiFetch(`/api/v1/corridors/${corridorId}/scoring-override`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["fp-rates"] });
      queryClient.invalidateQueries({ queryKey: ["calibration-suggestions"] });
      setSelectedCorridor(null);
    },
  });

  const handleSaveOverride = useCallback(
    (corridorId: number, body: ScoringOverride) => {
      overrideMutation.mutate({ corridorId, body });
    },
    [overrideMutation]
  );

  const handleAcceptSuggestion = useCallback(
    (suggestion: CalibrationSuggestion) => {
      overrideMutation.mutate({
        corridorId: suggestion.corridor_id,
        body: {
          corridor_multiplier_override: suggestion.suggested_multiplier,
          gap_duration_multiplier: 1.0,
          description: `Auto-accepted: ${suggestion.reason}`,
        },
      });
    },
    [overrideMutation]
  );

  const handleDismiss = useCallback((corridorId: number) => {
    setDismissedSuggestions((prev) => new Set(prev).add(corridorId));
  }, []);

  const activeSuggestions = suggestions?.filter(
    (s) => !dismissedSuggestions.has(s.corridor_id)
  );

  if (ratesLoading) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">FP Rate Tuning</h1>
        <p className="text-gray-500">Loading FP rate data...</p>
      </div>
    );
  }

  if (ratesError) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">FP Rate Tuning</h1>
        <p className="text-red-600">
          Failed to load FP rates. Ensure FP_TUNING_ENABLED is set.
        </p>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <h1 className="text-2xl font-bold mb-2">FP Rate Tuning</h1>
      <p className="text-gray-600 mb-6">
        Monitor false-positive rates by corridor and adjust scoring parameters.
      </p>

      {/* Region selector */}
      <RegionSelector selectedId={selectedRegion} onSelect={setSelectedRegion} />

      {/* Signal override editor */}
      <div className="mb-8">
        <h2 className="text-lg font-semibold mb-3">Signal Overrides</h2>
        <SignalOverrideEditor overrides={signalOverrides} onChange={setSignalOverrides} />
        {selectedRegion && (
          <ShadowScorePreview corridorId={selectedRegion} overrides={signalOverrides} />
        )}
      </div>

      {/* Chart */}
      {fpRates && fpRates.length > 0 && (
        <div className="mb-8">
          <h2 className="text-lg font-semibold mb-3">FP Rate by Corridor</h2>
          <FPRateByCorridorChart data={fpRates} />
        </div>
      )}

      {/* Calibration Suggestions */}
      {activeSuggestions && activeSuggestions.length > 0 && (
        <div className="mb-8">
          <h2 className="text-lg font-semibold mb-3">
            Calibration Suggestions
          </h2>
          <div className="space-y-3">
            {activeSuggestions.map((s) => (
              <div
                key={s.corridor_id}
                className="border rounded-lg p-4 bg-amber-50 flex items-start justify-between"
              >
                <div>
                  <p className="font-medium">{s.corridor_name}</p>
                  <p className="text-sm text-gray-700 mt-1">{s.reason}</p>
                  <p className="text-xs text-gray-500 mt-1">
                    Current: {s.current_multiplier}x | Suggested:{" "}
                    {s.suggested_multiplier}x | FP Rate:{" "}
                    {(s.fp_rate * 100).toFixed(1)}%
                  </p>
                </div>
                <div className="flex gap-2 ml-4 flex-shrink-0">
                  <button
                    onClick={() => handleAcceptSuggestion(s)}
                    className="px-3 py-1 bg-green-600 text-white text-sm rounded hover:bg-green-700"
                  >
                    Accept
                  </button>
                  <button
                    onClick={() => handleDismiss(s.corridor_id)}
                    className="px-3 py-1 border text-sm rounded hover:bg-gray-50"
                  >
                    Dismiss
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* FP Rate Table */}
      <h2 className="text-lg font-semibold mb-3">Corridor FP Rates</h2>
      <div className="overflow-x-auto">
        <table className="min-w-full border-collapse border">
          <thead>
            <tr className="bg-gray-100">
              <th className="border px-4 py-2 text-left">Corridor</th>
              <th className="border px-4 py-2 text-right">Alerts</th>
              <th className="border px-4 py-2 text-right">FPs</th>
              <th className="border px-4 py-2 text-right">FP Rate</th>
              <th className="border px-4 py-2 text-right">30d</th>
              <th className="border px-4 py-2 text-right">90d</th>
              <th className="border px-4 py-2 text-center">Trend</th>
              <th className="border px-4 py-2 text-center">Actions</th>
            </tr>
          </thead>
          <tbody>
            {fpRates?.map((r) => (
              <tr key={r.corridor_id} className={fpBgClass(r.fp_rate)}>
                <td className="border px-4 py-2 font-medium">
                  {r.corridor_name}
                </td>
                <td className="border px-4 py-2 text-right">
                  {r.total_alerts}
                </td>
                <td className="border px-4 py-2 text-right">
                  {r.false_positives}
                </td>
                <td
                  className="border px-4 py-2 text-right font-semibold"
                  style={{ color: fpColor(r.fp_rate) }}
                >
                  {(r.fp_rate * 100).toFixed(1)}%
                </td>
                <td
                  className="border px-4 py-2 text-right"
                  style={{ color: fpColor(r.fp_rate_30d) }}
                >
                  {(r.fp_rate_30d * 100).toFixed(1)}%
                </td>
                <td
                  className="border px-4 py-2 text-right"
                  style={{ color: fpColor(r.fp_rate_90d) }}
                >
                  {(r.fp_rate_90d * 100).toFixed(1)}%
                </td>
                <td className="border px-4 py-2 text-center">
                  {r.trend}
                  {trendArrow(r.trend)}
                </td>
                <td className="border px-4 py-2 text-center">
                  <button
                    onClick={() =>
                      setSelectedCorridor({
                        id: r.corridor_id,
                        name: r.corridor_name,
                      })
                    }
                    className="px-3 py-1 bg-blue-600 text-white text-sm rounded hover:bg-blue-700"
                  >
                    Override
                  </button>
                </td>
              </tr>
            ))}
            {(!fpRates || fpRates.length === 0) && (
              <tr>
                <td colSpan={8} className="border px-4 py-8 text-center text-gray-500">
                  No corridors with reviewed alerts found.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Calibration Timeline */}
      <div className="mt-8 mb-8">
        <h2 className="text-lg font-semibold mb-3">Calibration History</h2>
        <CalibrationTimeline corridorId={selectedRegion} />
      </div>

      {/* Override Modal */}
      {selectedCorridor && (
        <OverrideModal
          corridorId={selectedCorridor.id}
          corridorName={selectedCorridor.name}
          onClose={() => setSelectedCorridor(null)}
          onSave={handleSaveOverride}
        />
      )}
    </div>
  );
}
