/** Operations health (B3): one glance — database, channel connections, storage. */
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { Badge, Button, Card, Kpi, PageHeader, SectionTitle, StateBlock } from "@/components/ui";
import { ApiError_, api } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";

type OpsSummary = {
  database: string;
  integrations: {
    provider: string;
    connection_status: string;
    missing_fields: string[];
    last_webhook_at: string | null;
    last_error: string | null;
    last_test_status: string | null;
  }[];
  storage: { files: number; bytes: number };
};

export default function OpsPage() {
  const { t } = useTranslation();
  const ops = useQuery({ queryKey: ["ops", "summary"], queryFn: () => api.get<OpsSummary>("/ops/summary") });

  if (ops.isPending) return <StateBlock kind="loading" />;
  if (ops.isError)
    return ops.error instanceof ApiError_ && ops.error.status === 403 ? (
      <StateBlock kind="forbidden" />
    ) : (
      <StateBlock kind="error" action={<Button onClick={() => void ops.refetch()}>{t("common.retry")}</Button>} />
    );

  const data = ops.data;
  return (
    <>
      <PageHeader title={t("ops.title")} />
      <div className="grid gap-3 sm:grid-cols-3">
        <Kpi label={t("ops.database")} value={data.database} tone={data.database === "ok" ? "g" : "r"} />
        <Kpi label={t("ops.storage")} value={`${data.storage.files} / ${data.storage.bytes}`} tone="b" />
      </div>
      <Card className="mt-4">
        <SectionTitle>{t("settings.title")}</SectionTitle>
        {data.integrations.map((row) => (
          <div key={row.provider} className="flex flex-wrap items-center justify-between gap-2 border-b border-line py-2 text-[13px] last:border-0">
            <span className="font-bold text-ink">{row.provider}</span>
            <Badge tone={row.connection_status === "connected" ? "g" : row.connection_status === "error" ? "r" : "a"}>
              {t(`settings.connection.${row.connection_status === "connected" ? "connected" : row.connection_status === "error" ? "error" : "notConnected"}`)}
            </Badge>
            <span className="text-muted">
              {t("ops.lastWebhook")}: {row.last_webhook_at ? formatDateTime(row.last_webhook_at) : "—"}
            </span>
            {row.last_error ? <span className="text-danger">{t("ops.lastError")}: {row.last_error}</span> : null}
          </div>
        ))}
      </Card>
    </>
  );
}
