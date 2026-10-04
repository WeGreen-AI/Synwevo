/** Sharing a published form: its public link, and personal fill-in requests. */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Drawer, useDialog, useI18n, useToast } from "@synwevo/ui";
import { op, newKey } from "../../shared/ops";
import type { Definition } from "./logic";
import { Renderer } from "./Renderer";
import { failureText } from "./text";

export function CopyField({ value, label }: { value: string; label: string }) {
  const { t } = useI18n();
  const toast = useToast();
  return <div className="fm-copy">
    <input className="input sm mono" readOnly value={value} aria-label={label} onFocus={(e) => e.currentTarget.select()} />
    <button type="button" className="btn sm" onClick={async () => {
      try { await navigator.clipboard.writeText(value); toast(t("已复制"), "ok"); }
      catch { toast(t("无法自动复制，请手动选择链接复制"), "err"); }
    }}>{t("复制")}</button>
  </div>;
}

export function ShareDrawer({ form, ws, canWrite, onClose, onChanged }: {
  form: { id: string; title: string; published_version: number | null; public_link: { enabled: boolean; url: string | null } | null };
  ws: { id: string; name: string }; canWrite: boolean; onClose: () => void; onChanged: () => void;
}) {
  const { t } = useI18n();
  const dialog = useDialog();
  const toast = useToast();
  const [publicUrl, setPublicUrl] = useState<string | null>(form.public_link?.url ?? null);
  const enabled = !!form.public_link?.enabled;

  async function setPublic(on: boolean) {
    if (on && enabled && !(await dialog.confirm(t("换一个公开链接"), t("旧的公开链接会立即失效。确定生成新链接？"), t("生成新链接")))) return;
    if (!on && !(await dialog.confirm(t("关闭公开填写"), t("关闭后公开链接立即失效，正在填写的人将无法提交。"), t("关闭"), true))) return;
    try {
      const out = await op<{ url: string | null }>("forms.public_link.set", { form_id: form.id, enabled: on }, { workspace: ws.id });
      setPublicUrl(out.output.url);
      onChanged();
    } catch (e) { toast(failureText(t, e), "err"); }
  }

  return <Drawer title={t("分享与填写请求")} onClose={onClose}>
    <section className="fm-share-block">
      <h3>{t("公开链接")}</h3>
      <p className="muted small">{t("任何拿到链接的人都能填写，每人只看到自己的草稿与提交；看不到预填内容或其他人的回答。链接始终使用最新发布的版本。")}</p>
      {enabled && publicUrl && <CopyField value={publicUrl} label={t("公开链接")} />}
      {enabled && !publicUrl && <p className="muted small">{t("公开填写已开启。只有编辑者可以查看链接。")}</p>}
      {canWrite && <div className="row gap4 mt8">
        {!enabled ? <button type="button" className="btn" onClick={() => void setPublic(true)}>{t("开启公开填写")}</button> : <>
          <button type="button" className="btn ghost sm" onClick={() => void setPublic(true)}>{t("换一个链接")}</button>
          <button type="button" className="btn ghost sm" onClick={() => void setPublic(false)}>{t("关闭公开填写")}</button></>}
      </div>}
    </section>
    {canWrite && <NewRequest formId={form.id} ws={ws} />}
  </Drawer>;
}

/** A personal request: to one person, optionally prefilled, with a deadline. */
export function NewRequest({ formId, ws, onCreated }: { formId: string; ws: { id: string }; onCreated?: (id: string) => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const navigate = useNavigate();
  const [defn, setDefn] = useState<Definition | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [policy, setPolicy] = useState<"once" | "multiple">("once");
  const [signIn, setSignIn] = useState(false);
  const [prefill, setPrefill] = useState<Record<string, any>>({});
  const [showPrefill, setShowPrefill] = useState(false);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<{ request_id: string; url: string } | null>(null);
  const [key] = useState(newKey);

  useEffect(() => {
    let live = true;
    op<any>("forms.form.get", { form_id: formId }, { workspace: ws.id }).then(async (out) => {
      const v = out.output.published_version;
      if (!v || !live) return;
      // The request pins the latest published version; prefill is entered against that version.
      const ver = await op<any>("forms.form.version", { form_id: formId, version: v }, { workspace: ws.id });
      if (!live) return;
      setVersion(v);
      setDefn(ver.output.definition);
    }).catch(() => undefined);
    return () => { live = false; };
  }, [formId, ws.id]);

  async function create() {
    setBusy(true);
    try {
      const clean = Object.fromEntries(Object.entries(prefill).filter(([, v]) => v !== "" && v !== undefined && v !== null
        && !(Array.isArray(v) && v.length === 0)));
      const out = await op<{ request_id: string; url: string }>("forms.request.create", {
        form_id: formId, title: title.trim() || undefined, prefill: clean, submission_policy: policy,
        require_sign_in: signIn, due_at: due ? new Date(due).getTime() / 1000 : undefined,
        recipient: { email: email.trim() || undefined, name: name.trim() || undefined },
      }, { workspace: ws.id, key });
      setCreated(out.output);
      onCreated?.(out.output.request_id);
    } catch (e) {
      toast(failureText(t, e), "err");
    } finally { setBusy(false); }
  }

  if (created) return <section className="fm-share-block" role="status">
    <h3>{t("填写请求已创建")}</h3>
    <p className="muted small">{t("把链接发给填写人。收件人如果是平台用户，也会在通知中心收到提醒。")}</p>
    <CopyField value={created.url} label={t("填写链接")} />
    <div className="row gap4 mt8">
      <button type="button" className="btn sm" onClick={() => navigate(`/forms/w/${ws.id}/r/${created.request_id}`)}>{t("查看请求")}</button>
    </div>
  </section>;

  return <section className="fm-share-block">
    <h3>{t("发出填写请求")}</h3>
    <p className="muted small">{t("为一个人生成专属链接：可以预填内容、设置截止时间，并要求对方登录后填写。")}</p>
    {version && <p className="muted small">{t("使用版本 {v}", { v: version })}</p>}
    <div className="fm-prop-row">
      <label className="fm-prop"><span className="fm-label small">{t("收件人邮箱")}</span>
        <input className="input sm" type="email" value={email} maxLength={254} onChange={(e) => setEmail(e.target.value)} /></label>
      <label className="fm-prop"><span className="fm-label small">{t("收件人姓名")}</span>
        <input className="input sm" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} /></label>
    </div>
    <label className="fm-prop"><span className="fm-label small">{t("请求标题（默认用表单名称）")}</span>
      <input className="input sm" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} /></label>
    <div className="fm-prop-row">
      <label className="fm-prop"><span className="fm-label small">{t("截止时间（可选）")}</span>
        <input className="input sm" type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} /></label>
      <label className="fm-prop"><span className="fm-label small">{t("提交次数")}</span>
        <select className="input sm" value={policy} onChange={(e) => setPolicy(e.target.value as any)}>
          <option value="once">{t("只能提交一次")}</option><option value="multiple">{t("可以提交多次")}</option></select></label>
    </div>
    <label className="fm-choice"><input type="checkbox" checked={signIn} onChange={(e) => setSignIn(e.target.checked)} />
      <span>{t("要求收件人登录后填写（只有收件邮箱对应的账户能打开）")}</span></label>
    {defn && <details open={showPrefill} onToggle={(e) => setShowPrefill((e.target as HTMLDetailsElement).open)}>
      <summary>{t("预填内容（可选）")}</summary>
      <p className="muted small">{t("填写人会看到这些内容并可以修改。附件字段不能预填。")}</p>
      <Renderer definition={{ ...defn, fields: defn.fields.filter((f) => f.type !== "file" && f.type !== "section"), steps: [] }}
                answers={prefill} onChange={(id, v) => setPrefill((p) => ({ ...p, [id]: v }))} />
    </details>}
    <button type="button" className="btn primary mt8" disabled={busy || !version} onClick={() => void create()}>
      {busy ? t("创建中…") : t("创建填写请求")}</button>
  </section>;
}
