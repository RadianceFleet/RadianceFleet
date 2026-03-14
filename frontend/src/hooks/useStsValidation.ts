import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";

interface StsValidationPayload {
  stsId: number;
  user_validated: boolean;
  confidence_override?: number;
}

export function useStsValidation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ stsId, user_validated, confidence_override }: StsValidationPayload) => {
      const params = new URLSearchParams();
      params.set("user_validated", String(user_validated));
      if (confidence_override != null)
        params.set("confidence_override", String(confidence_override));
      return apiFetch(`/sts-events/${stsId}?${params}`, { method: "PATCH" });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sts-events"] }),
  });
}
