import { useEffect, useRef, useState } from "react";
import { api } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { FlowError, useAction } from "./AuthFlow";
import OrganizationAction from "./OrganizationAction";
import { orgPath, type Organization } from "./types";

const AVATAR_LIMIT = 256 * 1024;
/** Reject obvious invalid inputs before asking for sensitive-action reauthentication.
 * The server still decodes, canonicalizes and authorizes the submitted PNG. */
export function avatarDimensions(bytes: Uint8Array): { width: number; height: number } {
  const signature = [137, 80, 78, 71, 13, 10, 26, 10];
  if (bytes.length < 24 || bytes.length > AVATAR_LIMIT || signature.some((b, i) => bytes[i] !== b)
    || String.fromCharCode(...bytes.slice(12, 16)) !== "IHDR") throw new Error("请选择不超过 256 KB、512 × 512 像素的 PNG 图片。");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const width = view.getUint32(16); const height = view.getUint32(20);
  if (!width || !height || width > 512 || height > 512) throw new Error("请选择不超过 256 KB、512 × 512 像素的 PNG 图片。");
  return { width, height };
}

type Candidate = { base64: string; url: string; name: string };
export default function OrganizationAvatar({ organization, onChanged }: { organization: Organization; onChanged: () => void }) {
  const { t } = useI18n(); const action = useAction(); const input = useRef<HTMLInputElement>(null);
  const [candidate, setCandidate] = useState<Candidate | null>(null); const [remove, setRemove] = useState(false);
  const [revision, setRevision] = useState(0); const [loadFailed, setLoadFailed] = useState(false);
  const manage = organization.status === "active" && ["owner", "admin"].includes(organization.role);
  useEffect(() => () => { if (candidate) URL.revokeObjectURL(candidate.url); }, [candidate]);
  useEffect(() => { setLoadFailed(false); }, [organization.has_avatar, organization.updated_at, revision]);
  function choose(file: File) {
    void action.run(async () => {
      if (file.size > AVATAR_LIMIT) throw new Error("请选择不超过 256 KB、512 × 512 像素的 PNG 图片。");
      const bytes = new Uint8Array(await file.arrayBuffer()); avatarDimensions(bytes);
      let binary = "";
      for (let offset = 0; offset < bytes.length; offset += 8192) binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
      setCandidate({ base64: btoa(binary), url: URL.createObjectURL(file), name: file.name });
    });
  }
  function complete() { setCandidate(null); setRemove(false); setRevision(v => v + 1); onChanged(); }
  return <section className="org-section" aria-labelledby="organization-avatar-heading"><h2 id="organization-avatar-heading">{t("组织头像")}</h2>
    <div className="org-avatar-row">
      <div className="org-avatar">{organization.has_avatar && !loadFailed
        ? <img src={`${orgPath(organization.id)}/avatar?v=${organization.updated_at || 0}-${revision}`} alt={t("「{name}」的组织头像", { name: organization.name })} onError={() => setLoadFailed(true)} />
        : <span aria-label={t("组织名称首字")}>{Array.from(organization.name)[0]}</span>}</div>
      {manage && <div className="flow-stack"><div className="org-actions">
        <button className="btn" type="button" disabled={action.busy} onClick={() => input.current?.click()}>{t(organization.has_avatar ? "更换组织头像" : "上传组织头像")}</button>
        {organization.has_avatar && <button className="btn" type="button" disabled={action.busy} onClick={() => setRemove(true)}>{t("移除组织头像")}</button>}
        <input ref={input} type="file" accept="image/png" hidden aria-label={t("选择组织头像")} onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) choose(file); }} />
      </div><span className="tiny muted">{t("PNG 图片，不超过 256 KB 和 512 × 512 像素。更改需要确认身份和填写原因。")}</span></div>}
    </div>
    <FlowError error={action.error} />
    {loadFailed && <div className="notice" role="status">{t("组织头像暂时无法加载。")}{" "}<button className="btn ghost" type="button" onClick={() => setRevision(v => v + 1)}>{t("重试")}</button></div>}
    {(candidate || remove) && <OrganizationAction spec={{
      title: t(remove ? "移除组织头像" : "更新组织头像"),
      impact: t(remove ? "移除后，组织将显示名称首字。组织成员与工作空间保持不变。" : "确认将所选图片设置为此组织的头像，所有当前组织成员都可以看到。"),
      run: reason => api(`${orgPath(organization.id)}/avatar`, { method: remove ? "DELETE" : "PUT", body: remove ? { reason } : { image_base64: candidate!.base64, reason } }),
    }} onClose={() => { setCandidate(null); setRemove(false); }} onDone={complete}>
      {candidate && !remove && <div className="org-avatar-preview"><img src={candidate.url} alt={t("所选组织头像预览")} /><span className="tiny muted">{candidate.name}</span></div>}
    </OrganizationAction>}
  </section>;
}
