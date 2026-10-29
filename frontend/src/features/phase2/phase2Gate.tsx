/** Shared Phase-2 gate: the screens exist, the data stays behind a flag the admin can toggle. */
import { useQueryClient } from "@tanstack/react-query";
import { Power } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Button, Card, StateBlock } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { ApiError_, api } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { PHASE2_FLAG, usePhase2 } from "./usePhase2";

/**
 * The disabled panel. Phase 2 UI is shipped in full but the API answers 403 `feature_disabled`,
 * so the screen explains that instead of pretending to have data (spec §8.4).
 */
export function Phase2Gate({ onEnable, enabling }: { onEnable?: () => void; enabling?: boolean }) {
  const { t } = useTranslation();
  const { canAny } = useAuth();
  return (
    <Card className="mx-auto max-w-[560px] text-center">
      <div className="mx-auto grid h-11 w-11 place-items-center rounded-full" style={{ background: "var(--a-bg)", color: "var(--a-ink)" }}>
        <Power size={19} strokeWidth={1.8} />
      </div>
      <h2 className="mt-3 text-card font-bold text-ink">{t("phase2.disabled")}</h2>
      <p className="mx-auto mt-2 max-w-[420px] text-[13px] leading-[21px] text-muted">{t("phase2.disabledBody")}</p>
      {onEnable && canAny("settings.manage") ? (
        <Button variant="primary" busy={enabling} onClick={onEnable}>
          {t("phase2.flagOn")}
        </Button>
      ) : null}
    </Card>
  );
}

/** Wraps a Phase-2 screen: shows the gate when the flag is off, otherwise the children. */
export function Phase2Screen({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [enabling, setEnabling] = useState(false);
  const { flags, enabled } = usePhase2();
  const enable = async () => {
    setEnabling(true);
    try {
      await api.patch(`/feature-flags/${PHASE2_FLAG}`, { enabled: true });
      await queryClient.invalidateQueries({ queryKey: ["feature-flags"] });
      toast(t("phase2.flagOn"));
    } catch (error) {
      toast(error instanceof ApiError_ ? error.message : t("common.errorBody"), "error");
    } finally {
      setEnabling(false);
    }
  };

  if (flags.isPending) return <StateBlock kind="loading" />;
  if (flags.isError) return <StateBlock kind="error" action={<Button onClick={() => void flags.refetch()}>{t("common.retry")}</Button>} />;
  if (!enabled) return <Phase2Gate onEnable={() => void enable()} enabling={enabling} />;
  return <>{children}</>;
}
