import { useEffect, useMemo, useRef, useState } from "react";
import { PlatformNav } from "@synwevo/auth-client";
import { Link, useNavigate } from "react-router-dom";
import { del, get, patch, post } from "@synwevo/auth-client";
import { fmtTime, useDialog, useToast } from "@synwevo/ui";
import { jobStatus, ruleMode } from "../lib/status";
import MoreMenu from "../components/MoreMenu";

type Sort = "updated" | "name" | "status";

const SORTS: { k: Sort; label: string }[] = [
  { k: "updated", label: "最近更新" },
  { k: "name", label: "名称" },
  { k: "status", label: "最近任务状态" },
];

export default function Home() {
  const [projects, setProjects] = useState<any[] | null>(null);
  const [templates, setTemplates] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("updated");
  const [showArchived, setShowArchived] = useState(false);
  const [cursor, setCursor] = useState(0);
  const listRef = useRef<HTMLTableSectionElement>(null);
  const nav = useNavigate();
  const toast = useToast();
  const { prompt, confirm } = useDialog();

  const load = () => {
    get(`/api/projects${showArchived ? "?archived=1" : ""}`).then(setProjects).catch((e) => setErr(e.message));
    get("/api/templates").then(setTemplates).catch(() => {});
  };
  useEffect(load, [showArchived]);

  const create = async () => {
    const name = await prompt("新建项目", "项目名称", "新项目");
    if (name === null) return;
    const p = await post("/api/projects", { name });
    nav(`/p/${p.id}`);
  };

  /** Archiving is reversible, so it needs no confirmation — unlike deleting. */
  const archive = async (p: any, on: boolean) => {
    await patch(`/api/projects/${p.id}`, { archived: on });
    load();
    toast(on ? `已归档「${p.name}」，可在「显示已归档」中取消` : `已取消归档「${p.name}」`, "ok");
  };

  const rename = async (p: any) => {
    const n = await prompt("重命名项目", "名称", p.name);
    if (n) {
      await patch(`/api/projects/${p.id}`, { name: n });
      load();
    }
  };

  const remove = async (p: any) => {
    const ok = await confirm("删除项目",
      <>删除项目「{p.name}」？<br />如果只是想让它从列表里消失，用<b>归档</b>更合适——归档随时可以撤销。</>, "删除", true);
    if (!ok) return;
    await del(`/api/projects/${p.id}`);
    toast("已删除", "ok");
    load();
  };

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const list = (projects || []).filter((p) => {
      if (!needle) return true;
      return [p.name, p.template_name, ruleMode(p.record_rule?.mode)]
        .filter(Boolean).some((s: string) => String(s).toLowerCase().includes(needle));
    });
    const by: Record<Sort, (a: any, b: any) => number> = {
      updated: (a, b) => (b.updated_at || 0) - (a.updated_at || 0),
      name: (a, b) => String(a.name).localeCompare(String(b.name), "zh"),
      status: (a, b) => String(a.last_job?.status || "").localeCompare(String(b.last_job?.status || "")),
    };
    return [...list].sort(by[sort]);
  }, [projects, q, sort]);

  useEffect(() => { setCursor(0); }, [q, sort, showArchived]);
  useEffect(() => {
    listRef.current?.querySelectorAll<HTMLElement>("tr[data-row]")[cursor]?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.nativeEvent.isComposing || !rows.length) return;
    const mod = e.metaKey || e.ctrlKey;
    if (e.key === "ArrowDown" || (!mod && (e.key === "j" || e.key === "J"))) {
      e.preventDefault();
      setCursor((i) => Math.min(rows.length - 1, i + 1));
    } else if (e.key === "ArrowUp" || (!mod && (e.key === "k" || e.key === "K"))) {
      e.preventDefault();
      setCursor((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      nav(`/p/${rows[cursor].id}`);
    }
  };

  return (
    <div className="app">
      <div className="topbar">
        <span className="brand"><span className="logo">S</span>Synwevo Docs · 数据映射与模板批量生成</span>
        <span className="spacer" />
        <PlatformNav />
        <Link className="btn ghost" to="/capabilities">格式能力矩阵</Link>
        <Link className="btn ghost" to="/about">关于与许可</Link>
        <button className="btn primary" onClick={create}>＋ 新建项目</button>
      </div>
      <div style={{ overflow: "auto", flex: 1 }}>
        <div className="home">
          {err && <div className="notice err">{err}</div>}

          <div className="row mb8 wrap">
            <h1 className="grow" style={{ minWidth: 100 }}>项目</h1>
            <input className="input" style={{ width: 220 }} value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="搜索名称 / 模板…" aria-label="搜索项目" />
            <label className="row gap4 small muted nowrap">
              排序
              <select className="input sm" value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="排序方式">
                {SORTS.map((s) => <option key={s.k} value={s.k}>{s.label}</option>)}
              </select>
            </label>
            <label className="check small nowrap">
              <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
              显示已归档
            </label>
          </div>

          {projects && !projects.length && !q && (
            <div className="card card-b empty" style={{ height: "auto" }}>
              <div className="big">还没有项目</div>
              <div>一个项目 = 一批来源文件 + 一个目标模板 + 样本规则与映射。规则可保存后用于下一批同结构文件。</div>
              <button className="btn primary" onClick={create}>新建第一个项目</button>
            </div>
          )}

          {!!projects?.length && (
            <>
              <div className="card" style={{ overflow: "auto" }}>
                <table className="tbl" aria-label="项目列表">
                  <thead>
                    <tr><th>名称</th><th>模板</th><th>来源</th><th>样本规则</th><th>最近任务</th><th>更新</th><th /></tr>
                  </thead>
                  <tbody ref={listRef} tabIndex={0} onKeyDown={onKey} style={{ outline: "none" }}>
                    {rows.map((p, i) => {
                      const [label, tone] = jobStatus(p.last_job?.status);
                      return (
                        <tr key={p.id} data-row
                          className={`rowlink${i === cursor ? " cursor" : ""}${p.archived_at ? " off" : ""}`}
                          onClick={() => nav(`/p/${p.id}`)}>
                          <td>
                            <span style={{ fontWeight: 600 }}>{p.name}</span>
                            {p.archived_at ? <span className="badge" style={{ marginLeft: 6 }}>已归档</span> : null}
                          </td>
                          <td className="small muted">{p.template_name || "未设置"}</td>
                          <td className="small">{p.sources}</td>
                          <td className="small muted nowrap">{ruleMode(p.record_rule?.mode)}</td>
                          <td className="small nowrap">
                            {p.last_job
                              ? <><span className={`badge ${tone}`}>{label}</span>{" "}
                                <span className="muted">{p.last_job.counts?.success || 0}/{p.last_job.counts?.total || 0}</span></>
                              : <span className="muted">—</span>}
                          </td>
                          <td className="small muted nowrap">{fmtTime(p.updated_at)}</td>
                          <td className="nowrap" onClick={(e) => e.stopPropagation()}>
                            <MoreMenu label="⋯" items={[
                              { label: "打开", onClick: () => nav(`/p/${p.id}`) },
                              { label: "重命名…", onClick: () => rename(p) },
                              { label: p.archived_at ? "取消归档" : "归档（可撤销）", onClick: () => archive(p, !p.archived_at) },
                              { label: "删除…", onClick: () => remove(p) },
                            ]} />
                          </td>
                        </tr>
                      );
                    })}
                    {!rows.length && (
                      <tr><td colSpan={7} className="muted" style={{ textAlign: "center", padding: 16 }}>
                        没有匹配「{q}」的项目
                      </td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              <div className="tiny muted mt8">
                显示 {rows.length} / {projects.length} 个项目 · 列表内可用 ↑↓ 移动、回车打开
              </div>
            </>
          )}

          <div className="row mt16 mb8"><h1 className="grow" style={{ marginTop: 24 }}>模板库</h1></div>
          <div className="card" style={{ overflow: "auto" }}>
            <table className="tbl">
              <thead><tr><th>名称</th><th>格式</th><th>来源</th><th>字段</th><th>更新</th><th /></tr></thead>
              <tbody>
                {templates.map((t) => (
                  <tr key={t.id}>
                    <td>{t.name}</td><td>{t.format.toUpperCase()}</td>
                    <td className="small muted">{t.kind === "uploaded" ? t.original_name : t.kind === "builtin_doc" ? "内置文档编辑器" : "内置表格编辑器"}</td>
                    <td>{t.slot_count}</td><td className="small muted">{fmtTime(t.updated_at)}</td>
                    <td className="nowrap">
                      {(t.kind === "builtin_doc" || t.kind === "builtin_sheet") && <><Link className="btn xs" to={`/templates/${t.id}/edit`}>编辑</Link>{" "}</>}
                      <MoreMenu label="⋯" items={[
                        { label: "复制", onClick: async () => { await post(`/api/templates/${t.id}/duplicate`, {}); toast("已复制", "ok"); load(); } },
                        { label: "重命名…", onClick: async () => { const n = await prompt("重命名模板", "名称", t.name); if (n) { await patch(`/api/templates/${t.id}`, { name: n }); load(); } } },
                        { label: "删除…", onClick: async () => { if (await confirm("删除模板", `删除模板「${t.name}」？使用它的项目需要重新选择模板。`, "删除", true)) { await del(`/api/templates/${t.id}`); load(); } } },
                      ]} />
                    </td>
                  </tr>
                ))}
                {!templates.length && <tr><td colSpan={6} className="muted" style={{ textAlign: "center", padding: 16 }}>模板在项目中上传或新建后会出现在这里，可复制后用于其他项目</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="tiny muted mt16">所有文件只在本机/自有服务器处理，不会发送到外部服务。</div>
        </div>
      </div>
    </div>
  );
}
