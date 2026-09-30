import { useState } from "react";
import { useI18n } from "@synwevo/ui";
import { Loading } from "../../shared/states";
import { ErrorBox, Pager, useRead } from "./common";
import type { Member, Page, Selector as Value } from "./types";

export function PeoplePicker({ ws, value, onChange, disabled = false, exclude = [], single = false }: {
  ws: string; value: string[]; onChange: (v: string[]) => void; disabled?: boolean; exclude?: string[]; single?: boolean;
}) {
  const { t } = useI18n(); const [q, setQ] = useState(""); const [offset, setOffset] = useState(0);
  const result = useRead<Page<Member>>("members", { q, offset, limit: 50 }, ws);
  return <div className="ap-people"><label>{t("查找审批人")}<input className="input" disabled={disabled} value={q}
    onChange={e => { setQ(e.target.value); setOffset(0); }} /></label>
    {result.error ? <ErrorBox error={result.error} retry={result.reload} /> : !result.data ? <Loading /> : <>
      <div className="ap-candidates">{result.data.items.filter(m => !exclude.includes(m.id)).map(m => <label className="ap-choice" key={m.id}>
        <input type="checkbox" checked={value.includes(m.id)} disabled={disabled} onChange={e => onChange(single ? e.target.checked ? [m.id] : []
          : e.target.checked ? [...value, m.id] : value.filter(u => u !== m.id))} />{m.display_name}</label>)}</div>
      <Pager page={result.data} offset={offset} setOffset={setOffset} /></>}
    {value.length > 0 && <div className="ap-selected">{value.map(uid => <span className="badge" key={uid}>
      {result.data?.items.find(m => m.id === uid)?.display_name ?? uid}
      <button type="button" className="btn ghost sm" disabled={disabled} aria-label={t("移除审批人")} onClick={() => onChange(value.filter(u => u !== uid))}>×</button>
    </span>)}</div>}
  </div>;
}
export function ApproverSelector({ ws, value, onChange, disabled }: { ws: string; value: Value; onChange: (v: Value) => void; disabled?: boolean }) {
  const { t } = useI18n();
  return <><PeoplePicker ws={ws} value={value.users} onChange={users => onChange({ ...value, users })} disabled={disabled} />
    <label>{t("同时包括这些角色")}<select className="input" disabled={disabled} value={value.min_role ?? ""}
      onChange={e => onChange({ ...value, min_role: e.target.value || null })}>
      <option value="">{t("仅指定成员")}</option><option value="editor">{t("编辑者及以上")}</option>
      <option value="admin">{t("管理员及以上")}</option><option value="owner">{t("所有者")}</option></select></label></>;
}
