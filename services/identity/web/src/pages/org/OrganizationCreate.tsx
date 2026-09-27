import { useState } from "react";
import { api, useSession } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { FlowError, useAction } from "./AuthFlow";
import type { Organization, OrganizationWorkspace } from "./types";
import "../../organization.messages";

type Draft = { name: string; workspace_name: string; key: string };
export function creationAttempt(previous: Draft | null, name: string, workspace_name: string): Draft {
  return previous && previous.name === name && previous.workspace_name === workspace_name ? previous : { name, workspace_name, key: crypto.randomUUID() };
}
async function createOrganization(draft: Draft) {
  return api<{ organization: Organization; workspace: OrganizationWorkspace }>("/api/auth/organizations", {
    method: "POST", headers: { "Idempotency-Key": draft.key },
    body: { name: draft.name, workspace_name: draft.workspace_name },
  });
}
export default function OrganizationCreate({ onCreated }: { onCreated: (organization: Organization, workspace: OrganizationWorkspace) => Promise<void> | void }) {
  const { t } = useI18n(); const { me } = useSession(); const action = useAction(); const key = `synwevo.organization.create.${me?.id || "session"}`;
  const [draft, setDraft] = useState<Draft | null>(() => { try { const value = JSON.parse(sessionStorage.getItem(key) || "null"); return value && typeof value.name === "string" && typeof value.workspace_name === "string" && typeof value.key === "string" ? value : null; } catch { return null; } });
  const [name, setName] = useState(draft?.name || ""); const [workspace, setWorkspace] = useState(draft?.workspace_name || "");
  function submit(e: React.FormEvent) { e.preventDefault(); void action.run(async () => {
    if (!name.trim() || !workspace.trim()) throw new Error("请填写组织名称和工作空间名称。");
    const attempt = creationAttempt(draft, name.trim(), workspace.trim()); setDraft(attempt);
    // Store before sending; a refresh after a lost response must retry the same creation.
    try { sessionStorage.setItem(key, JSON.stringify(attempt)); } catch { /* In-memory key still protects retries on this page. */ }
    const result = await createOrganization(attempt);
    try { sessionStorage.removeItem(key); } catch { /* optional persistence */ }
    await onCreated(result.organization, result.workspace);
  }); }
  return <form className="org-create" onSubmit={submit} aria-busy={action.busy}><FlowError error={action.error} />
    <div className="field"><label htmlFor="org-name">{t("组织名称")}</label><input id="org-name" className="input" value={name} onChange={e => setName(e.target.value)} maxLength={100} autoComplete="organization" required disabled={action.busy} /></div>
    <div className="field"><label htmlFor="org-workspace">{t("首个工作空间名称")}</label><input id="org-workspace" className="input" value={workspace} onChange={e => setWorkspace(e.target.value)} maxLength={100} required disabled={action.busy} aria-describedby="org-workspace-hint" /><span id="org-workspace-hint" className="tiny muted">{t("创建后，你是组织和这个工作空间的所有者。")}</span></div>
    <button className="btn primary" disabled={action.busy}>{t(action.busy ? "创建中…" : "创建组织和工作空间")}</button>
    {action.error != null && <p className="tiny muted">{t("请求中断时保持名称不变再重试，不会重复创建。")}</p>}
  </form>;
}
