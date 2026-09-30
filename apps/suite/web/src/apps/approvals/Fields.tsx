import { useI18n } from "@synwevo/ui";
import type { Field } from "./types";

export function Fields({ fields, values, setValues, disabled = false }: { fields: Field[]; values: Record<string, string>;
  setValues: (v: Record<string, string>) => void; disabled?: boolean }) {
  const { t } = useI18n();
  return <div className="ap-fields">{fields.map(f => {
    const props = { id: f.id, className: "input", disabled, required: f.required, value: values[f.id] ?? "",
      onChange: (e: { target: { value: string } }) => setValues({ ...values, [f.id]: e.target.value }),
      "aria-describedby": f.help ? `${f.id}-help` : undefined };
    return <div className="ap-field" key={f.id}><label htmlFor={f.id}>{f.label} {f.currency ? `(${f.currency})` : ""}{f.required ? " *" : ""}</label>
      {f.type === "select" ? <select {...props}><option value="">{t("请选择")}</option>{f.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
        : f.type === "textarea" ? <textarea {...props} rows={4} maxLength={f.max_length ?? 10000} />
        : <input {...props} type={f.type === "date" ? "date" : "text"} inputMode={["money", "number"].includes(f.type) ? "decimal" : undefined} maxLength={f.max_length ?? 2000} />}
      {f.help && <small className="muted" id={`${f.id}-help`}>{f.help}</small>}
    </div>;
  })}</div>;
}
