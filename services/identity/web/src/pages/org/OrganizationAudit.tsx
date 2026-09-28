/** The organization's governance log (CI-101), for holders of org.audit.read: who changed what in
 *  the organization and its workspaces' membership — never document or message content, never
 *  anyone's sign-ins. The server decides what is in it; this only words it. */
import { useEffect, useRef, useState } from "react";
import { get } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { FlowError, Loading } from "./AuthFlow";
import { orgPath, type Organization } from "./types";

interface Person { kind: "member" | "outside" | "platform_admin" | "system"; id?: string; name?: string }
interface AuditItem {
  seq: number; at: number; action: string; result: "ok" | "denied" | "error"; actor: Person;
  workspace: { id: string; name: string } | null;
  object: { kind: string | null; name?: string | null; person?: Person; workspace?: string };
  detail: Record<string, unknown>;
}
type Category = "" | "organization" | "members" | "invitations" | "roles" | "imports" | "approvals";

const CATEGORY_LABEL: Record<Category, string> = { "": "全部", organization: "组织本身", members: "成员变动", invitations: "邀请记录", roles: "角色", imports: "批量导入", approvals: "审批" };
const ACTION_LABEL: Record<string, string> = {
  "organization.create": "创建了组织", "organization.update": "修改了组织设置", "organization.transfer": "交接了组织所有权",
  "organization.freeze": "冻结了组织", "organization.unfreeze": "解冻了组织", "organization.delete": "删除了组织", "organization.recover": "恢复了组织",
  "organization.avatar.update": "更换了组织头像", "organization.avatar.remove": "移除了组织头像",
  "organization.workspace.create": "创建了工作空间", "organization.workspace.adopt": "把工作空间纳入组织",
  "organization.member.role": "更改了成员的组织角色", "organization.member.remove": "移除了组织成员", "organization.member.leave": "退出了组织",
  "organization.role.create": "创建了自定义角色", "organization.role.update": "修改了自定义角色", "organization.role.delete": "删除了自定义角色",
  "organization.role.assign": "调整了成员的自定义角色",
  "organization.member_import.upload": "上传了成员名单", "organization.member_import.ocr": "识别了成员名单中的文字", "organization.member_import.plan": "生成了导入预览",
  "organization.member_import.confirm": "确认了批量导入", "organization.member_import.cancel": "取消了批量导入", "organization.member_import.retry": "重试了批量导入的失败行",
  "organization.member_import.finished": "批量导入执行完毕",
  "organization.copy.decide": "处理了项目复制申请",
  "invite.create": "发出了邀请", "invite.resend": "重新发送了邀请", "invite.revoke": "撤回了邀请", "invite.update": "修改了邀请",
  "invite.accept": "接受了邀请", "invite.decline": "拒绝了邀请",
  "member.role": "更改了工作空间角色", "member.remove": "从工作空间移除了成员", "member.leave": "退出了工作空间", "member.entitlement": "调整了应用使用资格",
  "workspace.ownership.transfer": "交接了工作空间所有权", "workspace.owner.consent": "同意接收工作空间所有权",
};
const ROLE_LABEL: Record<string, string> = { owner: "所有者", admin: "管理员", member: "成员", viewer: "查看者", editor: "编辑者",
  active: "正常", frozen: "已冻结", deleted: "已删除" };

export default function OrganizationAudit({ organization, mine }: { organization: Organization; mine: string[] }) {
  const { t, locale } = useI18n();
  const can = mine.includes("org.audit.read");
  const [category, setCategory] = useState<Category>("");
  const [items, setItems] = useState<AuditItem[] | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  const shown = useRef<Category>(""); useEffect(() => { shown.current = category; }, [category]);
  const url = (before?: number, cat: Category = category) => `${orgPath(organization.id)}/audit?limit=50${cat ? `&category=${cat}` : ""}${before ? `&before=${before}` : ""}`;
  useEffect(() => {
    if (!can) return;
    const abort = new AbortController(); setItems(null); setError(null);
    get<{ items: AuditItem[]; next_before: number | null }>(url(), abort.signal).then(x => { setItems(x.items); setNext(x.next_before); }).catch(e => { if (!abort.signal.aborted) setError(e); });
    return () => abort.abort();
  }, [organization.id, category, can]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!can) return null;
  const more = async () => {
    if (!next) return; setBusy(true);
    const asked = category;             // a page for the category shown when it was asked for, not a later one
    try {
      const x = await get<{ items: AuditItem[]; next_before: number | null }>(url(next, asked));
      if (shown.current === asked) { setItems(prev => [...(prev || []), ...x.items]); setNext(x.next_before); }
    } catch (e) { setError(e); } finally { setBusy(false); }
  };
  const who = (p: Person) => p.kind === "system" ? t("系统") : p.kind === "platform_admin" ? t("平台管理员")
    : p.kind === "outside" ? (p.name ? t("{name}（不在组织中）", { name: p.name }) : t("不在组织中的人")) : p.name || "—";
  const role = (v: unknown) => typeof v === "string" ? t(ROLE_LABEL[v] || v) : "";
  const details = (i: AuditItem) => {
    const d = i.detail; const out: string[] = [];
    const before = typeof d.before === "object" && d.before ? (d.before as Record<string, unknown>).role : d.before;
    const after = typeof d.after === "object" && d.after ? (d.after as Record<string, unknown>).role : d.after;
    const beforeName = typeof d.before === "object" && d.before ? (d.before as Record<string, unknown>).name : undefined;
    const afterName = typeof d.after === "object" && d.after ? (d.after as Record<string, unknown>).name : undefined;
    if (typeof beforeName === "string" && typeof afterName === "string" && beforeName !== afterName) out.push(t("名称：{from} → {to}", { from: beforeName, to: afterName }));
    if (typeof before === "string" && typeof after === "string") out.push(t("{from} → {to}", { from: role(before), to: role(after) }));
    else if (typeof d.role === "string") out.push(t("角色：{role}", { role: role(d.role) }));
    if (Array.isArray(d.added) && d.added.length) out.push(t("新增：{roles}", { roles: (d.added as string[]).join("、") }));
    if (Array.isArray(d.removed) && d.removed.length) out.push(t("移除：{roles}", { roles: (d.removed as string[]).join("、") }));
    if (typeof d.permissions === "number") out.push(t("{n} 项权限", { n: d.permissions }));
    if (typeof d.invite === "number" || typeof d.update === "number") out.push(t("邀请 {i}，更新 {u}", { i: Number(d.invite || 0), u: Number(d.update || 0) }));
    else if (typeof d.invited === "number" || typeof d.updated === "number") out.push(t("邀请 {i}，更新 {u}", { i: Number(d.invited || 0), u: Number(d.updated || 0) }));
    if (typeof d.enabled === "boolean") out.push(t(d.enabled ? "已开启" : "已关闭"));
    if (typeof d.reviewed === "number" && d.reviewed) out.push(t("已核对识别行 {n}", { n: d.reviewed }));
    if (d.action === "approve") out.push(t("批准")); else if (d.action === "reject") out.push(t("拒绝"));
    if (d.self_approved === true) out.push(t("申请人自行批准"));
    if (typeof d.reason === "string" && d.reason) out.push(t("原因：{reason}", { reason: d.reason }));
    return out;
  };
  return <section className="org-section" id="org-audit"><h2>{t("组织审计")}</h2>
    <p className="tiny muted">{t("组织与其工作空间的治理操作：成员、邀请、角色、批量导入、审批与组织设置。不含文档、消息内容和登录记录。")}</p>
    <FlowError error={error} />
    <div className="row wrap gap4" role="tablist">{(Object.keys(CATEGORY_LABEL) as Category[]).map(c => <button key={c || "all"} type="button" role="tab" aria-selected={category === c}
      className={`btn ${category === c ? "primary" : ""}`} onClick={() => setCategory(c)}>{t(CATEGORY_LABEL[c])}</button>)}</div>
    {!items ? <Loading /> : !items.length ? <p className="muted">{t("没有记录。")}</p> : <ul className="org-list">{items.map(i => <li key={i.seq}>
      <div><strong>{who(i.actor)}</strong> {t(ACTION_LABEL[i.action] || i.action)}
        {i.object.person && <> · {who(i.object.person)}</>}{i.object.name && <> · {i.object.name}</>}
        {(i.workspace || i.object.workspace) && <span className="muted"> · {i.workspace?.name || i.object.workspace}</span>}
        <div className="tiny muted">{new Date(i.at * 1000).toLocaleString(locale)}{details(i).map((x, n) => <span key={n}> · {x}</span>)}</div></div>
      {i.result !== "ok" && <div className="org-actions"><span className="badge err">{t(i.result === "denied" ? "被拒绝" : "出错")}</span></div>}
    </li>)}</ul>}
    {next && <div className="org-actions"><button type="button" className="btn" disabled={busy} onClick={() => void more()}>{t("加载更多")}</button></div>}
  </section>;
}
