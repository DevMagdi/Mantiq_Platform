/**
 * Screen 15 — users, roles and the permission switches. Toggling a switch writes the whole
 * permission list of the selected role (`PUT /roles/{id}/permissions`) and is audited server-side;
 * the system-admin role is deliberately locked so nobody can switch themselves out of the product.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck, UserCog } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Modal } from "@/components/Modal";
import { Avatar, Badge, Button, Card, PageHeader, SectionTitle, StateBlock, Toggle } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api } from "@/lib/api/client";
import { relativeTime } from "@/lib/format";
import type { PermissionItem, Role, StaffUser } from "@/lib/api/views";

/** The seven switches of the design sheet, mapped to the real permission slugs (spec §5.1). */
const SWITCHES: { key: string; label: string; on: string; off?: string }[] = [
  { key: "leadsAll", label: "users.switches.leadsAll", on: "leads.view_all" },
  { key: "requests", label: "users.switches.requests", on: "requests.manage" },
  { key: "clients", label: "users.switches.clients", on: "clients.view_all" },
  { key: "policies", label: "users.switches.policies", on: "policies.view" },
  { key: "commissions", label: "users.switches.commissions", on: "commissions.view" },
  { key: "reports", label: "users.switches.reports", on: "reports.view" },
  { key: "users", label: "users.switches.users", on: "users.manage" },
];

export default function UsersPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [roleId, setRoleId] = useState<string>("");
  const [creating, setCreating] = useState(false);

  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<StaffUser[]>("/users") });
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => api.get<Role[]>("/roles") });
  const permissions = useQuery({ queryKey: ["permissions"], queryFn: () => api.get<PermissionItem[]>("/permissions") });

  const active = useMemo(() => {
    const list = roles.data ?? [];
    return list.find((role) => role.id === roleId) ?? list.find((role) => role.code === "sales_rep") ?? list[0] ?? null;
  }, [roles.data, roleId]);

  const save = useMutation({
    mutationFn: (payload: { roleId: string; permissions: string[] }) =>
      api.put(`/roles/${payload.roleId}/permissions`, { permissions: payload.permissions }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["roles"] });
      await queryClient.invalidateQueries({ queryKey: ["users"] });
      toast(t("users.permsSaved"));
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  if (users.isPending || roles.isPending) return <StateBlock kind="loading" />;
  if (users.isError || roles.isError) {
    return <StateBlock kind="forbidden" action={<Button onClick={() => void users.refetch()}>{t("common.retry")}</Button>} />;
  }

  const locked = active?.is_system === true && active.code === "system_admin";

  function toggle(slug: string, next: boolean) {
    if (!active) return;
    const current = new Set(active.permissions ?? []);
    if (next) current.add(slug);
    else current.delete(slug);
    // the "always on" pair of a switch and its own-scope twin stays consistent:
    if (slug === "leads.view_all" && !next) current.add("leads.view_own");
    if (slug === "clients.view_all" && !next) current.add("clients.view_own");
    if (slug === "requests.manage") current.add("requests.view");
    save.mutate({ roleId: active.id, permissions: [...current] });
  }

  return (
    <>
      <PageHeader
        title={t("users.title")}
        subtitle={t("users.subtitle")}
        actions={
          <Button variant="primary" icon={<UserCog size={16} strokeWidth={1.8} />} onClick={() => setCreating(true)}>
            {t("users.newUser")}
          </Button>
        }
      />

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Card padded={false}>
          <div className="px-[20px] pt-[18px]">
            <SectionTitle action={<span className="text-[12px] text-muted">{t("users.footnote")}</span>}>{t("users.people")}</SectionTitle>
          </div>
          <ul className="px-[20px] pb-[18px]">
            {(users.data ?? []).map((row) => (
              <li key={String(row.id)} className="row-line flex items-center gap-3 py-3">
                <Avatar name={row.full_name} size={38} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13.5px] font-bold text-ink">{row.full_name}</p>
                  <p className="truncate text-[12px] text-muted" dir="ltr">
                    {row.email}
                  </p>
                  {/* the mobile the member was added with, so the row can be checked against the sheet */}
                  <p className="truncate text-[11.5px] text-muted" dir="ltr">
                    {row.mobile || t("common.none")}
                  </p>
                </div>
                <div className="hidden min-w-0 flex-col items-end gap-1 text-end sm:flex">
                  <Badge tone="b">{row.role_name ?? row.role}</Badge>
                  <span className="text-[11.5px] text-muted">{scopeLabel(row, t)}</span>
                  <span className="text-[11px] text-muted">{row.last_login_at ? relativeTime(row.last_login_at) : t("common.none")}</span>
                </div>
                <Badge tone={row.is_active ? "g" : "t"}>{row.is_active ? t("users.active") : t("users.inactive")}</Badge>
              </li>
            ))}
          </ul>
        </Card>

        <div className="space-y-5">
          <Card>
            <SectionTitle action={<ShieldCheck size={17} strokeWidth={1.8} className="text-[var(--primary-ink)]" />}>{t("users.permGroups")}</SectionTitle>
            <label className="label" htmlFor="role-picker">
              {t("users.colRole")}
            </label>
            <select id="role-picker" className="field" value={active?.id ?? ""} onChange={(event) => setRoleId(event.target.value)}>
              {(roles.data ?? []).map((role) => (
                <option key={role.id} value={role.id}>
                  {role.name}
                </option>
              ))}
            </select>

            {locked ? (
              <p className="mt-3 rounded-btn px-3 py-2 text-[12px] font-semibold" style={{ background: "var(--a-bg)", color: "var(--a-ink)" }}>
                {t("users.systemRoleLocked")}
              </p>
            ) : null}

            <div className="mt-4 space-y-1">
              {SWITCHES.map((item) => (
                <Toggle
                  key={item.key}
                  label={t(item.label)}
                  disabled={locked === true || save.isPending || !active}
                  checked={Boolean(active?.permissions?.includes(item.on))}
                  onChange={(next) => toggle(item.on, next)}
                />
              ))}
            </div>

            <p className="mt-4 text-[11.5px] leading-[18px] text-muted">
              {(permissions.data ?? []).length} {t("common.rows")} · {t("users.scope")}:{" "}
              {active?.code === "sales_rep" ? t("users.leadsOnly") : active?.code === "customer_service" ? t("users.scopedClients", { names: "2" }) : t("users.everything")}
            </p>
          </Card>

          <Card>
            <SectionTitle>{t("users.connection")}</SectionTitle>
            <ul className="space-y-2 text-[12.5px]">
              <li className="flex items-center justify-between border-b border-line pb-2">
                <span className="text-muted">{t("users.online")}</span>
                <span className="font-semibold text-ink">
                  {(users.data ?? []).filter((row) => row.last_login_at && Date.now() - new Date(row.last_login_at).getTime() < 15 * 60_000).length}
                </span>
              </li>
              <li className="flex items-center justify-between">
                <span className="text-muted">{t("users.lastSeen")}</span>
                <span className="font-semibold text-ink">{(users.data ?? []).filter((row) => !row.last_login_at).length}</span>
              </li>
            </ul>
          </Card>
        </div>
      </div>
      {creating ? (
        <NewUserDialog
          roles={roles.data ?? []}
          onClose={() => setCreating(false)}
          onCreated={async () => {
            await queryClient.invalidateQueries({ queryKey: ["users"] });
          }}
        />
      ) : null}
    </>
  );
}

function NewUserDialog({ roles, onClose, onCreated }: { roles: Role[]; onClose: () => void; onCreated: () => void | Promise<void> }) {
  const { t } = useTranslation();
  const toast = useToast();
  const [form, setForm] = useState({ email: "", full_name: "", password: "", role_code: roles[0]?.code ?? "", mobile: "", is_active: true });
  // a generated password is kept on screen until it has been copied; a toast would fade with the only copy
  const [issued, setIssued] = useState<{ email: string; password: string } | null>(null);

  const create = useMutation({
    mutationFn: () =>
      api.post<{ id: string; email: string; role: string; temporary_password?: string | null }>("/users", {
        ...form,
        // blank means "the server generates one", and the answer carries it back exactly once
        password: form.password.trim(),
      }),
    onSuccess: async (created) => {
      await onCreated();
      const password = created.temporary_password;
      if (password) setIssued({ email: created.email, password });
      else {
        toast(t("users.userCreated"));
        onClose();
      }
    },
    onError: (error: Error) => toast(error.message, "error"),
  });
  const valid = form.email.includes("@") && form.full_name.trim().length >= 2 && (form.password.trim().length === 0 || form.password.trim().length >= 10);

  if (issued !== null) {
    return (
      <Modal
        title={t("users.newUser")}
        onClose={onClose}
        actions={
          <Button variant="primary" onClick={onClose}>
            {t("common.close")}
          </Button>
        }
      >
        <p className="text-[13px] leading-[22px] text-muted">{t("users.issuedNote")}</p>
        <dl className="mt-3 space-y-2 rounded-card border border-line bg-[var(--kanban-col)] px-3 py-2.5 text-[13px]">
          <div className="flex items-center justify-between gap-3">
            <dt className="text-muted">{t("auth.email")}</dt>
            <dd className="font-semibold text-ink" dir="ltr">
              {issued.email}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-3">
            <dt className="text-muted">{t("users.fieldPassword")}</dt>
            <dd className="select-all font-mono font-bold text-ink" dir="ltr" data-testid="user-issued-password">
              {issued.password}
            </dd>
          </div>
        </dl>
      </Modal>
    );
  }

  return (
    <Modal
      title={t("users.newUser")}
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
      <label className="label" htmlFor="nu-name">
        {t("users.colUser")}
      </label>
      <input id="nu-name" className="field" value={form.full_name} onChange={(event) => setForm({ ...form, full_name: event.target.value })} />
      <label className="label mt-3" htmlFor="nu-email">
        {t("auth.email")}
      </label>
      <input id="nu-email" dir="ltr" className="field" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} />
      <label className="label mt-3" htmlFor="nu-mobile">
        {t("users.colMobile")}
      </label>
      <input id="nu-mobile" dir="ltr" className="field" placeholder={t("users.mobileHint")} value={form.mobile} onChange={(event) => setForm({ ...form, mobile: event.target.value })} />
      <label className="label mt-3" htmlFor="nu-password">
        {t("auth.password")}
      </label>
      <input id="nu-password" type="password" dir="ltr" className="field" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} />
      <p className="mt-1 text-[11.5px] text-muted">{t("users.passwordOptionalHint")}</p>
      <label className="label mt-3" htmlFor="nu-role">
        {t("users.colRole")}
      </label>
      <select id="nu-role" className="field" value={form.role_code} onChange={(event) => setForm({ ...form, role_code: event.target.value })}>
        {roles.map((role) => (
          <option key={role.id} value={role.code}>
            {role.name}
          </option>
        ))}
      </select>
      <div className="mt-4 flex items-center justify-between gap-3 rounded-btn bg-[var(--kanban-col)] px-3 py-2">
        <span className="text-[12.5px] font-semibold text-ink">{t("users.activateNow")}</span>
        <Toggle label={t("users.activateNow")} checked={form.is_active} onChange={(next) => setForm({ ...form, is_active: next })} />
      </div>
    </Modal>
  );
}

function scopeLabel(row: StaffUser, t: (key: string, options?: Record<string, unknown>) => string): string {
  if (row.role === "sales_rep") return t("users.leadsOnly");
  if (row.scopes && row.scopes.length > 0) return t("users.scopedClients", { names: String(row.scopes.length) });
  return t("users.allClients");
}
