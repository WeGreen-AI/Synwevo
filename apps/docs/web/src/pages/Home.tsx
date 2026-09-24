import { useCallback, useEffect, useRef, useState } from "react";
import { PlatformNav, useSession } from "@synwevo/auth-client";
import { Link, useNavigate } from "react-router-dom";
import { del, get, patch, post, qs } from "@synwevo/auth-client";
import { fmtTime, isImeKey, useDialog, useToast } from "@synwevo/ui";
import { jobStatus, ruleMode } from "../lib/status";
import { errorText, isPermissionError, permissions, READ_ONLY_TEXT } from "../lib/perm";
import { clampCursor, defaultOrder, listQuery, PAGE_SIZE, toPage, type ListParams, type Page, type ProjectSort } from "../lib/projects";
import { useLatest } from "../lib/latest";
import MoreMenu from "../components/MoreMenu";

const SORTS: { k: ProjectSort; label: string }[] = [
  { k: "updated", label: "最近更新" },
  { k: "created", label: "创建时间" },
  { k: "name", label: "名称" },
];

export default function Home() {
  const [params, setParams] = useState<ListParams>({ q: "", sort: "updated", order: "desc", offset: 0, limit: PAGE_SIZE, archived: false });
  const [typed, setTyped] = useState("");                  // the search box; params.q follows it after a pause
  const [page, setPage] = useState<Page<any> | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [templates, setTemplates] = useState<any[] | null>(null);
  const [tplErr, setTplErr] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const listRef = useRef<HTMLTableSectionElement>(null);
  const nav = useNavigate();
  const toast = useToast();
  const { prompt, confirm } = useDialog();
  const latest = useLatest();
  const session = useSession();
  const perm = permissions(session.me, session.disabled);
  const ro = !perm.canWrite;

  // Search, sort and paging happen on the server (the list used to download every project, and
  // aggregate every tenant's sources and jobs, on each visit). An older server that ignores the
  // parameters returns the whole array, and toPage() applies the same rules on the client.
  const load = useCallback(() => {
    setLoading(true);
    latest(
      (signal) => get(`/api/projects${qs(listQuery(params))}`, signal),
      (r) => { setPage(toPage(r, params)); setErr(null); setLoading(false); },
      (e) => { setErr(errorText(e)); setLoading(false); },
    );
  }, [params, latest]);
  useEffect(load, [load]);

  const loadTemplates = useCallback(() => {
    get("/api/templates").then((t) => { setTemplates(t); setTplErr(null); }).catch((e) => setTplErr(errorText(e)));
  }, []);
  useEffect(loadTemplates, [loadTemplates]);

  useEffect(() => {
    const t = setTimeout(() => setParams((p) => (p.q === typed ? p : { ...p, q: typed, offset: 0 })), 250);
    return () => clearTimeout(t);
  }, [typed]);

  const rows = page?.items || [];
  useEffect(() => { setCursor((c) => clampCursor(c, rows.length)); }, [rows.length]);
  useEffect(() => { setCursor(0); }, [params.q, params.sort, params.order, params.archived, params.offset]);
  useEffect(() => {
    listRef.current?.querySelectorAll<HTMLElement>("tr[data-row]")[cursor]?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  /** Run a change and report a refusal. Every one of these used to be a bare `await`, so a 409 or
   *  a viewer's 403 did nothing visible and left the list as it was. */
  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    try {
      await fn();
      if (ok) toast(ok, "ok");
      return true;
    } catch (e: any) {
      toast(errorText(e), isPermissionError(e) ? "info" : "err");
      return false;
    }
  };

  const create = async () => {
    const name = await prompt("新建项目", "项目名称", "新项目");
    if (name === null) return;
    try {
      const p = await post("/api/projects", { name });
      nav(`/p/${p.id}`);
    } catch (e: any) {
      toast(errorText(e), "err");
    }
  };

  /** Archiving is reversible, so it needs no confirmation — unlike deleting. */
  const archive = async (p: any, on: boolean) => {
    if (await act(() => patch(`/api/projects/${p.id}`, { archived: on }),
      on ? `已归档「${p.name}」，可在「显示已归档」中取消` : `已取消归档「${p.name}」`)) load();
  };

  const rename = async (p: any) => {
    const n = await prompt("重命名项目", "名称", p.name);
    if (n && n !== p.name && await act(() => patch(`/api/projects/${p.id}`, { name: n }))) load();
  };

  const remove = async (p: any) => {
    const ok = await confirm("删除项目",
      <>删除项目「{p.name}」？<br />如果只是想让它从列表里消失，用<b>归档</b>更合适——归档随时可以撤销。</>, "删除", true);
    if (ok && await act(() => del(`/api/projects/${p.id}`), "已删除")) load();
  };

  const setSort = (sort: ProjectSort) => setParams((p) => ({ ...p, sort, order: defaultOrder(sort), offset: 0 }));

  const onKey = (e: React.KeyboardEvent) => {
    // Keys pressed on a link or on the ⋯ menu inside a row belong to that control. Enter on
    // 重命名/删除 used to be swallowed here and open whichever project the cursor was on.
    if (e.target !== e.currentTarget || isImeKey(e) || !rows.length) return;
    const mod = e.metaKey || e.ctrlKey;
    if (e.key === "ArrowDown" || (!mod && (e.key === "j" || e.key === "J"))) {
      e.preventDefault();
      setCursor((i) => clampCursor(i + 1, rows.length));
    } else if (e.key === "ArrowUp" || (!mod && (e.key === "k" || e.key === "K"))) {
      e.preventDefault();
      setCursor((i) => clampCursor(i - 1, rows.length));
    } else if (e.key === "Enter") {
      const r = rows[clampCursor(cursor, rows.length)];
      if (r) { e.preventDefault(); nav(`/p/${r.id}`); }
    }
  };

  const first = page ? page.offset + 1 : 0;
  const last = page ? page.offset + rows.length : 0;
  const searching = !!params.q;

  return (
    <div className="app">
      <div className="topbar">
        {/* The tagline is desktop-only: "Synwevo Docs · 数据映射与模板批量生成" is long enough that
            at 375px it wrapped to three lines and pushed the top bar over its own content.
            Every other page already shows just the product name there. */}
        <span className="brand">
          <span className="logo" aria-hidden="true">S</span>Synwevo Docs
          <span className="desktop-only"> · 数据映射与模板批量生成</span>
        </span>
        <span className="spacer" />
        <PlatformNav />
        <Link className="btn ghost" to="/capabilities">格式能力矩阵</Link>
        <Link className="btn ghost" to="/about">关于与许可</Link>
        {!ro && <button type="button" className="btn primary" onClick={create}>＋ 新建项目</button>}
      </div>
      <div style={{ overflow: "auto", flex: 1 }}>
        <div className="home">
          <div className="row mb8 wrap">
            <h1 className="grow" style={{ minWidth: 100 }}>项目</h1>
            <input className="input" type="search" style={{ width: 220 }} value={typed} onChange={(e) => setTyped(e.target.value)}
              placeholder="搜索名称 / 模板…" aria-label="搜索项目" />
            <label className="row gap4 small muted nowrap">
              排序
              <select className="input sm" value={params.sort} onChange={(e) => setSort(e.target.value as ProjectSort)} aria-label="排序方式">
                {SORTS.map((s) => <option key={s.k} value={s.k}>{s.label}</option>)}
              </select>
            </label>
            <button type="button" className="btn sm" onClick={() => setParams((p) => ({ ...p, order: p.order === "asc" ? "desc" : "asc", offset: 0 }))}
              aria-label={params.order === "asc" ? "当前升序，切换为降序" : "当前降序，切换为升序"} title="切换升序 / 降序">
              {params.order === "asc" ? "↑ 升序" : "↓ 降序"}
            </button>
            <label className="check small nowrap">
              <input type="checkbox" checked={params.archived} onChange={(e) => setParams((p) => ({ ...p, archived: e.target.checked, offset: 0 }))} />
              显示已归档
            </label>
          </div>
          {ro && <div className="notice small mb8">{READ_ONLY_TEXT}</div>}

          {err && (
            <div className="notice err row mb8" role="alert">
              <span className="grow">项目列表加载失败：{err}</span>
              <button type="button" className="btn sm" onClick={load}>重试</button>
            </div>
          )}

          {!err && page && page.total === 0 && !searching && (
            <div className="card card-b empty" style={{ height: "auto" }}>
              <div className="big">{params.archived ? "没有项目" : "还没有项目"}</div>
              <div>一个项目 = 一批来源文件 + 一个目标模板 + 样本规则与映射。规则可保存后用于下一批同结构文件。</div>
              {!ro && <button type="button" className="btn primary" onClick={create}>新建第一个项目</button>}
            </div>
          )}

          {(!page && loading && !err) && <div className="empty" style={{ height: "auto" }}>加载项目…</div>}

          {page && (page.total > 0 || searching) && (
            <>
              <div className="card" style={{ overflow: "auto" }} aria-busy={loading}>
                <table className="tbl" aria-label="项目列表" aria-rowcount={page.total + 1}>
                  <thead>
                    <tr><th>名称</th><th>模板</th><th>来源</th><th>样本规则</th><th>最近任务</th><th>更新</th><th><span className="sr-only">操作</span></th></tr>
                  </thead>
                  {/* The tbody is one tab stop for ↑↓ / J K and Enter; each name is also a real
                      link, so Tab, Enter, middle-click and screen-reader link lists all work. */}
                  <tbody ref={listRef} tabIndex={rows.length ? 0 : -1} onKeyDown={onKey} aria-label="项目（↑↓ 移动，回车打开）">
                    {rows.map((p, i) => {
                      const [label, tone] = jobStatus(p.last_job?.status);
                      return (
                        <tr key={p.id} data-row aria-rowindex={page.offset + i + 2}
                          className={`rowlink${i === cursor ? " cursor" : ""}${p.archived_at ? " off" : ""}`}
                          onClick={(e) => { if (!(e.target as HTMLElement).closest("a, button")) nav(`/p/${p.id}`); }}>
                          <td>
                            <Link className="projlink" to={`/p/${p.id}`}>{p.name}</Link>
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
                          <td className="nowrap">
                            <MoreMenu label="⋯" ariaLabel={`项目「${p.name}」的操作`} items={[
                              { label: "打开", onClick: () => nav(`/p/${p.id}`) },
                              ...(ro ? [] : [
                                { label: "重命名…", onClick: () => rename(p) },
                                { label: p.archived_at ? "取消归档" : "归档（可撤销）", onClick: () => archive(p, !p.archived_at) },
                                { label: "删除…", onClick: () => remove(p) },
                              ]),
                            ]} />
                          </td>
                        </tr>
                      );
                    })}
                    {!rows.length && (
                      <tr><td colSpan={7} className="muted" style={{ textAlign: "center", padding: 16 }}>
                        没有匹配「{params.q}」的项目
                      </td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              <div className="row mt8 wrap tiny muted">
                <span className="grow" aria-live="polite">
                  {rows.length ? `第 ${first}–${last} 个，共 ${page.total} 个项目` : `共 ${page.total} 个项目`}
                  {loading ? " · 加载中…" : ""} · 列表内可用 ↑↓ 移动、回车打开
                </span>
                <button type="button" className="btn sm" disabled={page.offset <= 0 || loading}
                  onClick={() => setParams((p) => ({ ...p, offset: Math.max(0, p.offset - p.limit) }))}>‹ 上一页</button>
                <button type="button" className="btn sm" disabled={page.next_offset == null || loading}
                  onClick={() => setParams((p) => ({ ...p, offset: page.next_offset ?? p.offset }))}>下一页 ›</button>
              </div>
            </>
          )}

          <div className="row mt16 mb8"><h1 className="grow" style={{ marginTop: 24 }}>模板库</h1></div>
          {tplErr && (
            <div className="notice err row mb8" role="alert">
              <span className="grow">模板库加载失败：{tplErr}</span>
              <button type="button" className="btn sm" onClick={loadTemplates}>重试</button>
            </div>
          )}
          <div className="card" style={{ overflow: "auto" }}>
            <table className="tbl" aria-label="模板库">
              <thead><tr><th>名称</th><th>格式</th><th>来源</th><th>字段</th><th>更新</th><th><span className="sr-only">操作</span></th></tr></thead>
              <tbody>
                {(templates || []).map((t) => (
                  <tr key={t.id}>
                    <td>{t.name}</td><td>{t.format.toUpperCase()}</td>
                    <td className="small muted">{t.kind === "uploaded" ? t.original_name : t.kind === "builtin_doc" ? "内置文档编辑器" : "内置表格编辑器"}</td>
                    <td>{t.slot_count}</td><td className="small muted">{fmtTime(t.updated_at)}</td>
                    <td className="nowrap">
                      {!ro && (t.kind === "builtin_doc" || t.kind === "builtin_sheet") && <><Link className="btn xs" to={`/templates/${t.id}/edit`}>编辑</Link>{" "}</>}
                      {!ro && (
                        <MoreMenu label="⋯" ariaLabel={`模板「${t.name}」的操作`} items={[
                          { label: "复制", onClick: async () => { if (await act(() => post(`/api/templates/${t.id}/duplicate`, {}), "已复制")) loadTemplates(); } },
                          { label: "重命名…", onClick: async () => { const n = await prompt("重命名模板", "名称", t.name); if (n && n !== t.name && await act(() => patch(`/api/templates/${t.id}`, { name: n }))) loadTemplates(); } },
                          { label: "删除…", onClick: async () => { if (await confirm("删除模板", `删除模板「${t.name}」？使用它的项目需要重新选择模板。`, "删除", true) && await act(() => del(`/api/templates/${t.id}`), "已删除模板")) { loadTemplates(); load(); } } },
                        ]} />
                      )}
                    </td>
                  </tr>
                ))}
                {templates && !templates.length && !tplErr && <tr><td colSpan={6} className="muted" style={{ textAlign: "center", padding: 16 }}>模板在项目中上传或新建后会出现在这里，可复制后用于其他项目</td></tr>}
                {!templates && !tplErr && <tr><td colSpan={6} className="muted" style={{ textAlign: "center", padding: 16 }}>加载模板…</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="tiny muted mt16">所有文件只在本机/自有服务器处理，不会发送到外部服务。</div>
        </div>
      </div>
    </div>
  );
}
