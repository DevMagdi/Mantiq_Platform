/**
 * The sales office roster on screen 6: who sits where, add a seat, move or switch one off.
 *
 * The role list is read from `GET /sales-team/members` (`assignable_roles`) rather than copied here: the
 * server owns which seats this screen may hand out, and a duplicated list would offer an option the API
 * refuses — the mistake ADR-016 §5 wrote down.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UserPlus } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Modal } from "@/components/Modal";
import { Avatar, Badge, Button, Card, DataTable, SectionTitle, StateBlock, Toggle } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api } from "@/lib/api/client";
import type { TeamMember } from "@/lib/api/views";

type Roster = {
  members: TeamMember[];
  assignable_roles: { code: string; name: string }[];
};

export function TeamMembersCard() {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const roster = useQuery({
    queryKey: ["sales-team", "members"],
    queryFn: () => api.get<Roster>("/sales-team/members"),
  });
  const [adding, setAdding] = useState(false);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["sales-team"] });
    await queryClient.invalidateQueries({ queryKey: ["sales"] });
  };

  const patch = useMutation({
    mutationFn: (input: { id: string; body: Record<string, unknown> }) => api.patch(`/sales-team/members/${input.id}`, input.body),
    onSuccess: async () => {
      await refresh();
      toast(t("team.saved"));
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  const rows = roster.data?.members ?? [];
  const roles = roster.data?.assignable_roles ?? [];
  const roleLabel = (code: string) => roles.find((role) => role.code === code)?.name ?? code;

  return (
    <Card padded={false}>
      <div className="px-[20px] pt-[18px]">
        <SectionTitle
          action={
            <Button variant="primary" size="sm" icon={<UserPlus size={15} strokeWidth={1.8} />} onClick={() => setAdding(true)}>
              {t("team.add")}
            </Button>
          }
        >
          {t("team.title")}
        </SectionTitle>
      </div>
      <div className="px-[20px] pb-[18px]">
        {roster.isPending ? (
          <StateBlock kind="loading" />
        ) : roster.isError ? (
          <StateBlock kind="error" action={<Button onClick={() => void roster.refetch()}>{t("common.retry")}</Button>} />
        ) : (
          <DataTable<TeamMember>
            columns={[
              {
                id: "who",
                head: t("team.colMember"),
                render: (row) => (
                  <span className="flex items-center gap-2">
                    <Avatar name={row.full_name ?? ""} size={28} />
                    <span className="min-w-0">
                      <span className="block truncate text-[13px] font-bold text-ink">{row.full_name}</span>
                      <span className="block truncate text-[11.5px] text-muted" dir="ltr">
                        {row.email}
                      </span>
                    </span>
                  </span>
                ),
              },
              {
                id: "role",
                head: t("team.colRole"),
                render: (row) => (
                  <select
                    aria-label={`${t("team.colRole")}: ${row.full_name}`}
                    className="field !py-1 !text-[12px]"
                    value={row.role ?? ""}
                    disabled={patch.isPending}
                    onChange={(event) =>
                      patch.mutate({
                        id: String(row.id),
                        body: { role_code: event.target.value },
                      })
                    }
                  >
                    {roles.map((role) => (
                      <option key={role.code} value={role.code}>
                        {role.name}
                      </option>
                    ))}
                  </select>
                ),
              },
              {
                id: "load",
                head: t("team.colLoad"),
                width: 90,
                render: (row) => <span className="tabular-nums">{row.open_leads ?? 0}</span>,
              },
              {
                id: "active",
                head: t("team.colActive"),
                width: 120,
                render: (row) => (
                  <span className="flex items-center justify-end gap-2">
                    <Badge tone={row.is_active ? "g" : "t"}>{row.is_active ? t("team.active") : t("team.inactive")}</Badge>
                    <Toggle
                      label={t("team.colActive")}
                      checked={Boolean(row.is_active)}
                      onChange={(next) =>
                        patch.mutate({
                          id: String(row.id),
                          body: { is_active: next },
                        })
                      }
                    />
                  </span>
                ),
              },
            ]}
            rows={rows}
            keyOf={(row) => String(row.id)}
            empty={<p className="py-3 text-[13px] text-muted">{t("team.empty")}</p>}
          />
        )}
        <p className="mt-3 text-[11.5px] leading-[18px] text-muted">
          {t("team.footnote", {
            roles: roles.map((role) => roleLabel(role.code)).join(" · "),
          })}
        </p>
      </div>

      {adding ? (
        <MemberDialog
          roles={roles}
          onClose={() => setAdding(false)}
          onCreated={async () => {
            await refresh();
            toast(t("team.created"));
          }}
        />
      ) : null}
    </Card>
  );
}

function MemberDialog({ roles, onClose, onCreated }: { roles: { code: string; name: string }[]; onClose: () => void; onCreated: () => void | Promise<void> }) {
  const { t } = useTranslation();
  const toast = useToast();
  const [form, setForm] = useState({
    full_name: "",
    email: "",
    mobile: "",
    role_code: roles[0]?.code ?? "",
    password: "",
    is_active: true,
  });
  // a generated password is shown once, here, and never in a toast that fades away
  const [issued, setIssued] = useState<{
    email: string;
    password: string;
  } | null>(null);
  const create = useMutation({
    mutationFn: () =>
      api.post<{
        id: string;
        email: string;
        role: string;
        temporary_password?: string | null;
      }>("/sales-team/members", {
        ...form,
        // an empty password means "generate one"; the answer carries it once and it is never stored in the audit row
        password: form.password.trim(),
      }),
    onSuccess: async (created) => {
      await onCreated();
      const password = created.temporary_password;
      if (password) setIssued({ email: created.email, password });
      else onClose();
    },
    onError: (error: Error) => toast(error.message, "error"),
  });
  const valid = form.full_name.trim().length >= 2 && form.email.includes("@") && (form.password.trim().length === 0 || form.password.trim().length >= 10);

  if (issued !== null) {
    // the one-time answer stays on screen until the manager has copied it — a toast that fades is not a
    // delivery mechanism for a password
    return (
      <Modal
        title={t("team.add")}
        onClose={onClose}
        actions={
          <Button variant="primary" onClick={onClose}>
            {t("common.close")}
          </Button>
        }
      >
        <p className="text-[13px] leading-[22px] text-muted">{t("team.issuedNote")}</p>
        <dl className="mt-3 space-y-2 rounded-card border border-line bg-[var(--kanban-col)] px-3 py-2.5 text-[13px]">
          <div className="flex items-center justify-between gap-3">
            <dt className="text-muted">{t("auth.email")}</dt>
            <dd className="font-semibold text-ink" dir="ltr">
              {issued.email}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-3">
            <dt className="text-muted">{t("team.fieldPassword")}</dt>
            <dd className="select-all font-mono font-bold text-ink" dir="ltr" data-testid="member-issued-password">
              {issued.password}
            </dd>
          </div>
        </dl>
      </Modal>
    );
  }

  return (
    <Modal
      title={t("team.add")}
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button variant="primary" busy={create.isPending} disabled={!valid} onClick={() => create.mutate()}>
            {t("common.save")}
          </Button>
        </>
      }
    >
      <label className="label" htmlFor="member-name">
        {t("team.fieldName")}
      </label>
      <input id="member-name" className="field" value={form.full_name} onChange={(event) => setForm({ ...form, full_name: event.target.value })} />

      <label className="label mt-3" htmlFor="member-email">
        {t("auth.email")}
      </label>
      <input id="member-email" dir="ltr" className="field" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} />

      <label className="label mt-3" htmlFor="member-mobile">
        {t("team.fieldMobile")}
      </label>
      <input id="member-mobile" dir="ltr" className="field" value={form.mobile} onChange={(event) => setForm({ ...form, mobile: event.target.value })} />

      <label className="label mt-3" htmlFor="member-role">
        {t("team.colRole")}
      </label>
      <select id="member-role" className="field" value={form.role_code} onChange={(event) => setForm({ ...form, role_code: event.target.value })}>
        {roles.map((role) => (
          <option key={role.code} value={role.code}>
            {role.name}
          </option>
        ))}
      </select>

      <label className="label mt-3" htmlFor="member-password">
        {t("team.fieldPassword")}
      </label>
      <input
        id="member-password"
        dir="ltr"
        type="text"
        className="field"
        placeholder={t("team.generatePlaceholder")}
        value={form.password}
        onChange={(event) => setForm({ ...form, password: event.target.value })}
      />
      <p className="mt-1 text-[11.5px] leading-[18px] text-muted">{t("team.passwordHint")}</p>

      <div className="mt-4 flex items-center justify-between gap-3 rounded-btn bg-[var(--kanban-col)] px-3 py-2">
        <span className="text-[12.5px] font-semibold text-ink">{t("team.activateNow")}</span>
        <Toggle label={t("team.activateNow")} checked={form.is_active} onChange={(next) => setForm({ ...form, is_active: next })} />
      </div>
    </Modal>
  );
}
