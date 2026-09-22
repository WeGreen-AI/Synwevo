import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { del, get, patch, post } from "../lib/api";
import { fmtTime, useDialog, useToast } from "../lib/ui";

export default function Home() {
  const [projects, setProjects] = useState<any[] | null>(null);
  const [templates, setTemplates] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const nav = useNavigate();
  const toast = useToast();
  const { prompt, confirm } = useDialog();
  const load = () => {
    get("/api/projects").then(setProjects).catch((e) => setErr(e.message));
    get("/api/templates").then(setTemplates).catch(() => {});
  };
  useEffect(load, []);

  const create = async () => {
    const name = await prompt("新建项目", "项目名称", "新项目");
    if (name === null) return;
    const p = await post("/api/projects", { name });
    nav(`/p/${p.id}`);
  };

  return (
    <div className="app">
      <div className="topbar">
        <span className="brand"><span className="logo">W</span>DocWeave 数据映射与模板批量生成</span>
        <span className="spacer" />
        <Link className="btn ghost" to="/capabilities">格式能力矩阵</Link>
        <Link className="btn ghost" to="/about">关于与许可</Link>
        <button className="btn primary" onClick={create}>＋ 新建项目</button>
      </div>
      <div style={{ overflow: "auto", flex: 1 }}>
        <div className="home">
          {err && <div className="notice err">{err}</div>}
          <div className="row mb8"><h1 className="grow">项目</h1></div>
          {projects && !projects.length && (
            <div className="card card-b empty" style={{ height: "auto" }}>
              <div className="big">还没有项目</div>
              <div>一个项目 = 一批来源文件 + 一个目标模板 + 样本规则与映射。规则可保存后用于下一批同结构文件。</div>
              <button className="btn primary" onClick={create}>新建第一个项目</button>
            </div>
          )}
          <div className="projgrid">
            {projects?.map((p) => (
              <div key={p.id} className="card projcard" onClick={() => nav(`/p/${p.id}`)}>
                <div className="row"><h3 className="grow ellipsis">{p.name}</h3>
                  <button className="btn xs ghost" onClick={async (e) => { e.stopPropagation(); const n = await prompt("重命名项目", "名称", p.name); if (n) { await patch(`/api/projects/${p.id}`, { name: n }); load(); } }}>重命名</button>
                  <button className="btn xs ghost" onClick={async (e) => { e.stopPropagation(); if (await confirm("删除项目", `删除项目「${p.name}」？项目将从列表中移除。`, "删除", true)) { await del(`/api/projects/${p.id}`); toast("已删除", "ok"); load(); } }}>删除</button>
                </div>
                <div className="small muted mt8">来源文件 {p.sources} 个 · {p.record_rule?.mode ? { per_row: "每行一份", per_file: "每文件一份", per_group: "每组一份" }[p.record_rule.mode as string] : "未定义样本"}</div>
                <div className="small muted">更新于 {fmtTime(p.updated_at)}</div>
                {p.last_job && <div className="small mt8">最近任务：<span className={`badge ${p.last_job.status === "completed" ? "ok" : p.last_job.status === "running" ? "info" : "warn"}`}>{p.last_job.status}</span> 成功 {p.last_job.counts?.success || 0} / {p.last_job.counts?.total || 0}</div>}
              </div>
            ))}
          </div>
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
                      {(t.kind === "builtin_doc" || t.kind === "builtin_sheet") && <Link className="btn xs" to={`/templates/${t.id}/edit`}>编辑</Link>}{" "}
                      <button className="btn xs" onClick={async () => { await post(`/api/templates/${t.id}/duplicate`, {}); toast("已复制", "ok"); load(); }}>复制</button>{" "}
                      <button className="btn xs" onClick={async () => { const n = await prompt("重命名模板", "名称", t.name); if (n) { await patch(`/api/templates/${t.id}`, { name: n }); load(); } }}>重命名</button>{" "}
                      <button className="btn xs danger" onClick={async () => { if (await confirm("删除模板", `删除模板「${t.name}」？使用它的项目需要重新选择模板。`, "删除", true)) { await del(`/api/templates/${t.id}`); load(); } }}>删除</button>
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
