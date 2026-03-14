import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import { buildQueryParams } from "../utils/queryParams";
import type { VesselTargetProfile, SearchMission, HuntCandidate } from "../types/api";

export function useHuntTargets(filters?: { skip?: number; limit?: number }) {
  const params = buildQueryParams({ skip: filters?.skip, limit: filters?.limit });
  return useQuery({
    queryKey: ["hunt-targets", filters],
    queryFn: () => apiFetch<VesselTargetProfile[]>(`/hunt/targets?${params}`),
  });
}

export function useHuntTarget(profileId: number | undefined) {
  return useQuery({
    queryKey: ["hunt-target", profileId],
    queryFn: () => apiFetch<VesselTargetProfile>(`/hunt/targets/${profileId}`),
    enabled: profileId != null,
  });
}

export function useCreateHuntTarget() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { vessel_id: number; last_lat?: number; last_lon?: number }) =>
      apiFetch<VesselTargetProfile>("/hunt/targets", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt-targets"] }),
  });
}

export function useHuntMissions(filters?: { skip?: number; limit?: number }) {
  const params = buildQueryParams({ skip: filters?.skip, limit: filters?.limit });
  return useQuery({
    queryKey: ["hunt-missions", filters],
    queryFn: () => apiFetch<SearchMission[]>(`/hunt/missions?${params}`),
  });
}

export function useHuntMission(missionId: number | undefined) {
  return useQuery({
    queryKey: ["hunt-mission", missionId],
    queryFn: () => apiFetch<SearchMission>(`/hunt/missions/${missionId}`),
    enabled: missionId != null,
  });
}

export function useCreateHuntMission() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      target_profile_id: number;
      search_start_utc: string;
      search_end_utc: string;
    }) =>
      apiFetch<SearchMission>("/hunt/missions", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt-missions"] }),
  });
}

export function useHuntCandidates(
  missionId: number | undefined,
  filters?: { skip?: number; limit?: number }
) {
  const params = buildQueryParams({ skip: filters?.skip, limit: filters?.limit });
  return useQuery({
    queryKey: ["hunt-candidates", missionId, filters],
    queryFn: () =>
      apiFetch<{ items: HuntCandidate[]; total: number }>(
        `/hunt/missions/${missionId}/candidates?${params}`
      ),
    enabled: missionId != null,
  });
}

export function useAnalyzeHuntMission() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (missionId: number) =>
      apiFetch<{ items: HuntCandidate[]; total: number }>(`/hunt/missions/${missionId}/analyze`, {
        method: "POST",
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hunt-candidates"] });
      qc.invalidateQueries({ queryKey: ["hunt-missions"] });
    },
  });
}

export function useFinalizeHuntMission() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ missionId, candidateId }: { missionId: number; candidateId: number }) =>
      apiFetch<SearchMission>(`/hunt/missions/${missionId}/finalize`, {
        method: "PUT",
        body: JSON.stringify({ candidate_id: candidateId }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hunt-missions"] });
      qc.invalidateQueries({ queryKey: ["hunt-candidates"] });
    },
  });
}
