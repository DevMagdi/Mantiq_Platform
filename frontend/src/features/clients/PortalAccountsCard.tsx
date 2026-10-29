/**
 * Portal logins for one client company (ADR-022, spec §6).
 *
 * Lives on the client file, not in the users screen: a portal account only means something when it is
 * pinned to one client, so it is created where the client is. Two deliberate UI rules:
 *
 * - **the temporary password is shown once, in a modal that cannot be reopened.** There is no endpoint that
 *   returns it again, so the screen has to say so instead of pretending a "copy later" exists.
 * - **deactivating is offered, deleting is not.** A portal account carries the request history of the
 *   company's owner; the API refuses the login instead of erasing who said what.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Plus, UserRoundCheck, UserRoundX } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Modal } from "@/components/Modal";
import { asText, lower } from "@/lib/text";
import { Badge, Button, Card, SectionTitle, StateBlock } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api } from "@/lib/api/client";
import { formatDate } from "@/lib/format";

type PortalAccount = {
  id: string;
  email: string;
  username?: string | null;
  full_name?: string | null;
  is_active: boolean;
  last_login_at?: string | null;
  portal_role?: string | null;
};

type CreatedAccount = PortalAccount & { temporary_password?: string | null; client_name?: string };

export function PortalAccountsCard({ clientId }: { clientId: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [creating, setCreating] = useState(false);
  const [issued, setIssued] = useState<{ username: string; password: string } | null>(null);

  const accounts = useQuery({
    queryKey: ["clients", clientId, "portal-accounts"],
    queryFn: () => api.get<PortalAccount[]>(`/clients/${clientId}/portal-accounts`),
  });

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ["clients", clientId, "portal-accounts"] });
  }

  const create = useMutation({
    mutationFn: (payload: { full_name?: string; username?: string; email?: string }) =>
      api.post<CreatedAccount>(`/clients/${clientId}/portal-accounts`, payload),
    onSuccess: async (account) => {
      await refresh();
      setCreating(false);
      // the password appears here and nowhere else — hence a modal the staff member must close
      setIssued({ username: account.username ?? account.email, password: account.temporary_password ?? "" });
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  /** The exact text a staff member sends — one source for the block and for the copy button. */
  const handoff = issued
    ? t("clients.portal.handoffBody", {
        url: typeof window === "undefined" ? "/login" : `${window.location.origin}/login`,
        username: issued.username,
        password: issued.password || "—",
      })
    : "";

  const reset = useMutation({
    mutationFn: (id: string) => api.post<CreatedAccount>(`/clients/${clientId}/portal-accounts/${id}/password`, {}),
    onSuccess: async (result) => {
      await refresh();
      setIssued({ username: result.username ?? result.email, password: result.temporary_password ?? "" });
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  const toggle = useMutation({
    mutationFn: (input: { id: string; is_active: boolean }) =>
      api.patch<PortalAccount>(`/clients/${clientId}/portal-accounts/${input.id}`, { is_active: input.is_active }),
    onSuccess: async (account) => {
      await refresh();
      toast(account.is_active ? t("clients.portal.enabled") : t("clients.portal.disabled"));
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  return (
    <Card>
      <SectionTitle
        action={
          <Button size="sm" icon={<Plus size={14} strokeWidth={1.8} />} onClick={() => setCreating(true)}>
            {t("clients.portal.create")}
          </Button>
        }
      >
        {t("clients.portal.title")}
      </SectionTitle>

      {accounts.isPending ? (
        <StateBlock kind="loading" />
      ) : accounts.isError ? (
        <StateBlock kind="error" body={t("common.errorBody")} />
      ) : (accounts.data ?? []).length === 0 ? (
        <p className="text-[12.5px] text-muted">{t("clients.portal.empty")}</p>
      ) : (
        <ul className="space-y-2">
          {(accounts.data ?? []).map((account) => (
            <li key={account.id} className="flex items-center justify-between gap-2 border-t border-line pt-2 first:border-t-0 first:pt-0">
              <div className="min-w-0">
                <p className="truncate text-[12.5px] font-semibold text-ink" dir="ltr">
                  {account.username ?? account.email}
                </p>
                <p className="text-[11px] text-muted">
                  {account.is_active ? t("clients.portal.lastLogin", { date: account.last_login_at ? formatDate(account.last_login_at) : "—" }) : t("clients.portal.isDisabled")}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                <Badge tone={account.is_active ? "g" : "r"}>{account.is_active ? t("clients.portal.active") : t("clients.portal.inactive")}</Badge>
                <Button size="sm" variant="ghost" icon={<KeyRound size={13} strokeWidth={1.8} />} onClick={() => reset.mutate(account.id)}>
                  {t("clients.portal.reset")}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  icon={account.is_active ? <UserRoundX size={13} strokeWidth={1.8} /> : <UserRoundCheck size={13} strokeWidth={1.8} />}
                  onClick={() => toggle.mutate({ id: account.id, is_active: !account.is_active })}
                >
                  {account.is_active ? t("clients.portal.disable") : t("clients.portal.enable")}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {creating ? <CreatePortalAccountDialog busy={create.isPending} onClose={() => setCreating(false)} onSubmit={(payload) => create.mutate(payload)} /> : null}

      {issued ? (
        <Modal
          title={t("clients.portal.issuedTitle")}
          onClose={() => setIssued(null)}
          actions={
            <>
              <Button
                icon={<Copy size={14} strokeWidth={1.8} />}
                onClick={() => {
                  void navigator.clipboard?.writeText(`${issued.username} / ${issued.password}`);
                  toast(t("clients.portal.copied"));
                }}
              >
                {t("clients.portal.copy")}
              </Button>
              <Button variant="primary" onClick={() => setIssued(null)}>
                {t("common.close")}
              </Button>
            </>
          }
        >
          <p className="text-[12.5px] text-muted">{t("clients.portal.issuedNote")}</p>
          {/*
            The handoff, not just the facts: what a staff member has to do next is *send this*, and a screen
            that shows two fields and a copy button leaves them to compose the message themselves — with the
            password pasted into the wrong chat (R7). The block is select-all, and the warning is next to it.
          */}
          <div className="mt-3 rounded-btn border border-line bg-[var(--kanban-col)] p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[12px] font-semibold text-ink">{t("clients.portal.handoffTitle")}</span>
              <Button
                size="sm"
                variant="ghost"
                icon={<Copy size={13} strokeWidth={1.8} />}
                onClick={() => {
                  void navigator.clipboard?.writeText(handoff);
                  toast(t("clients.portal.copied"));
                }}
              >
                {t("clients.portal.copyMessage")}
              </Button>
            </div>
            <pre data-testid="portal-handoff" className="mt-2 select-all whitespace-pre-wrap break-words text-[12px] leading-[20px] text-ink" dir="ltr">
              {handoff}
            </pre>
            <p className="mt-2 text-[11.5px] text-muted">{t("clients.portal.handoffWarning")}</p>
          </div>

          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-[13px]">
            <dt className="text-muted">{t("clients.portal.username")}</dt>
            <dd className="font-bold text-ink" dir="ltr">
              {issued.username}
            </dd>
            <dt className="text-muted">{t("clients.portal.password")}</dt>
            <dd className="select-all font-mono font-bold text-ink" dir="ltr">
              {issued.password || "—"}
            </dd>
          </dl>
        </Modal>
      ) : null}
    </Card>
  );
}

function CreatePortalAccountDialog({
  busy,
  onClose,
  onSubmit,
}: {
  busy: boolean;
  onClose: () => void;
  onSubmit: (payload: { full_name?: string; username?: string; email?: string }) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");

  const usernameOk = username === "" || /^[a-z0-9][a-z0-9._-]{2,62}$/.test(username);
  const valid = (username.trim().length >= 3 && usernameOk) || email.trim().includes("@");

  return (
    <Modal
      title={t("clients.portal.create")}
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button
            variant="primary"
            busy={busy}
            disabled={!valid}
            onClick={() =>
              onSubmit({
                ...(name.trim() ? { full_name: name.trim() } : {}),
                ...(asText(username).trim() ? { username: lower(asText(username).trim()) } : {}),
                ...(email.trim() ? { email: email.trim() } : {}),
              })
            }
          >
            {t("common.confirm")}
          </Button>
        </>
      }
    >
      <label className="label" htmlFor="portal-name">
        {t("clients.portal.fullName")}
      </label>
      <input id="portal-name" className="field" value={name} onChange={(event) => setName(event.target.value)} />

      <label className="label mt-3" htmlFor="portal-username">
        {t("clients.portal.username")}
      </label>
      <input
        id="portal-username"
        className="field"
        dir="ltr"
        placeholder="alf.owner"
        value={username}
        onChange={(event) => setUsername(event.target.value)}
      />
      {!usernameOk ? <p className="mt-1 text-[12px] text-[var(--r-ink)]">{t("clients.portal.usernameRule")}</p> : null}

      <label className="label mt-3" htmlFor="portal-email">
        {t("clients.portal.emailOptional")}
      </label>
      <input id="portal-email" className="field" dir="ltr" value={email} onChange={(event) => setEmail(event.target.value)} />

      <p className="mt-3 text-[12px] text-muted">{t("clients.portal.passwordHint")}</p>
    </Modal>
  );
}
