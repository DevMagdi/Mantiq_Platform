/** The one flag that unlocks the Phase-2 screens (policies, issuing, reports) and its read hook. */
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import type { FeatureFlag } from "@/lib/api/views";

export const PHASE2_FLAG = "phase2.enabled";

export function usePhase2() {
  const flags = useQuery({ queryKey: ["feature-flags"], queryFn: () => api.get<FeatureFlag[]>("/feature-flags") });
  const flag = (flags.data ?? []).find((row) => row.key === PHASE2_FLAG) ?? null;
  return { flags, flag, enabled: flag?.enabled === true };
}
