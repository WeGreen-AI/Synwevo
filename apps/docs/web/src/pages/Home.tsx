import { formatNumber, LanguageMenu, BrandMark } from "@synwevo/ui";
import { serverText, issueText } from "../lib/presentation";
import { t as tr, useI18n } from "@synwevo/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import { PlatformNav, useSession } from "@synwevo/auth-client";
import { Link, useNavigate } from "react-router-dom";
import { del, get, patch, post, qs } from "@synwevo/auth-client";
import { fmtTime, isImeKey, useDialog, useToast } from "@synwevo/ui";
import { jobStatus, ruleMode } from "../lib/status";
import { errorText, inOrganizationWorkspace, isPermissionError, permissions, READ_ONLY_TEXT } from "../lib/perm";
import { clampCursor, defaultOrder, listQuery, PAGE_SIZE, toPage, type ListParams, type Page, type ProjectSort } from "../lib/projects";
import { useLatest } from "../lib/latest";
import MoreMenu from "../components/MoreMenu";
import CopyRequestDialog from "../components/CopyRequestDialog";

const SORTS: { k: ProjectSort; label: string }[] = [
  { k: "updated", label: "最近更新" },
  { k: "created", label: "创建时间" },
  { k: "name", label: "名称" },
];

export default function Home() {
  useI18n();
  const [params, setParams] = useState<ListParams>({ q: "", sort: "updated", order: "desc", offset: 0, limit: PAGE_SIZE, archived: false });
  const [typed, setTyped] = useState("");                  // the search box; params.q follows it after a pause
  const [page, setPage] = useState<Page<any> | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [templates, setTemplates] = useState<any[] | null>(null);
  const [copyFor, setCopyFor] = useState<{ id: string; name: string } | null>(null);
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
  // The copy-to-organization action exists only in a personal workspace: an organization
  // project has nothing to ask approval for, and the server refuses such a request anyway.
  const personal = session.me?.workspaces?.find((w) => w.id === session.me?.workspace_id)?.kind === "personal";
  // an organization workspace can publish templates to its organization and use what others published
  const inOrg = inOrganizationWorkspace(session.me);
  const [shared, setShared] = useState<{ organization: { id: string; name: string } | null; items: any[] } | null>(null);
  const [sharedErr, setSharedErr] = useState<string | null>(null);
  const loadShared = useCallback(() => {
    if (!inOrg) return;
    get("/api/organization-templates?include_deprecated=1").then((x) => { setShared(x); setSharedErr(null); }).catch((e) => setSharedErr(errorText(e)));
  }, [inOrg]);
  useEffect(loadShared, [loadShared]);
  const sharing = (id: string) => shared?.items.find((x) => x.source_template_id === id);
  const shareItems = (t: any) => {
    if (!inOrg) return [];
    const pub = sharing(t.id);
    if (!pub) return [{ label: tr("发布到组织…"), onClick: async () => {
      const d = await prompt(tr("发布到组织"), tr("说明（可选）"), "", tr("发布"));
      if (d !== null && await act(() => post(`/api/templates/${t.id}/publish`, { description: d }), tr("已发布到组织"))) { loadTemplates(); loadShared(); }
    } }];
    const items = [];
    if (!pub.deprecated) {
      items.push({ label: tr("更新组织发布"), disabled: !pub.source_ahead, title: pub.source_ahead ? tr("源模板有新版本，可更新发布") : undefined,
        onClick: async () => { const r: any = await post(`/api/organization-templates/${pub.id}/update`, {}).catch((e) => { toast(errorText(e), "err"); return null; }); if (r) { toast(tr("已发布第 {v1} 次", { v1: r.release_no }), "ok"); loadTemplates(); loadShared(); } } });
      items.push({ label: tr("弃用共享…"), onClick: async () => { if (await confirm(tr("弃用共享模板"), tr("弃用后组织成员不能再用它建立新项目；已复制到各工作空间的模板与任务不受影响。"), tr("弃用"), true) && await act(() => post(`/api/organization-templates/${pub.id}/deprecate`, {}), tr("已弃用"))) { loadTemplates(); loadShared(); } } });
    } else {
      items.push({ label: tr("恢复共享"), onClick: async () => { if (await act(() => post(`/api/organization-templates/${pub.id}/deprecate`, { deprecated: false }), tr("已恢复共享"))) { loadTemplates(); loadShared(); } } });
    }
    return items;
  };

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
    const name = await prompt(tr("新建项目"), tr("项目名称"), tr("新项目"));
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
      on ? tr("已归档「{v1}」，可在「显示已归档」中取消", { v1: p.name }) : tr("已取消归档「{v1}」", { v1: p.name }))) load();
  };

  const rename = async (p: any) => {
    const n = await prompt(tr("重命名项目"), tr("名称"), p.name);
    if (n && n !== p.name && await act(() => patch(`/api/projects/${p.id}`, { name: n, base_settings_rev: p.settings_rev }))) load();
  };

  const remove = async (p: any) => {
    const ok = await confirm(tr("删除项目"),
      tr("删除项目「{v1}」？只想让它从列表里消失时，用「归档」更合适，随时可以撤销。", { v1: p.name }), tr("删除"), true);
    if (ok && await act(() => del(`/api/projects/${p.id}`), tr("已删除"))) load();
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
          <BrandMark product="Docs" markBelow={720} />
          <span className="desktop-only brand-tagline">{tr(" · 数据映射与模板批量生成")}</span>
        </span>
        <span className="spacer" />
        <PlatformNav />
        {session.disabled && <LanguageMenu />}
        <Link className="btn ghost" to="/capabilities">{tr("格式能力矩阵")}</Link>
        <Link className="btn ghost" to="/about">{tr("关于与许可")}</Link>
        {!ro && <button type="button" className="btn primary" onClick={create}>{tr("＋ 新建项目")}</button>}
      </div>
      <div style={{ overflow: "auto", flex: 1 }}>
        <div className="home">
          <div className="row mb8 wrap">
            <h1 className="grow" style={{ minWidth: 100 }}>{tr("项目")}</h1>
            <input className="input" type="search" style={{ width: 220 }} value={typed} onChange={(e) => setTyped(e.target.value)}
              placeholder={tr("搜索名称 / 模板…")} aria-label={tr("搜索项目")} />
            <label className="row gap4 small muted nowrap">{tr("排序")}<select className="input sm" value={params.sort} onChange={(e) => setSort(e.target.value as ProjectSort)} aria-label={tr("排序方式")}>
                {SORTS.map((s) => <option key={s.k} value={s.k}>{tr(s.label)}</option>)}
              </select>
            </label>
            <button type="button" className="btn sm" onClick={() => setParams((p) => ({ ...p, order: p.order === "asc" ? "desc" : "asc", offset: 0 }))}
              aria-label={params.order === "asc" ? tr("当前升序，切换为降序") : tr("当前降序，切换为升序")} title={tr("切换升序 / 降序")}>
              {params.order === "asc" ? tr("↑ 升序") : tr("↓ 降序")}
            </button>
            <label className="check small nowrap">
              <input type="checkbox" checked={params.archived} onChange={(e) => setParams((p) => ({ ...p, archived: e.target.checked, offset: 0 }))} />{tr("显示已归档")}</label>
          </div>
          {ro && <div className="notice small mb8">{tr(READ_ONLY_TEXT)}</div>}

          {err && (
            <div className="notice err row mb8" role="alert">
              <span className="grow">{tr("项目列表加载失败：")}{serverText(err)}</span>
              <button type="button" className="btn sm" onClick={load}>{tr("重试")}</button>
            </div>
          )}

          {!err && page && page.total === 0 && !searching && (
            <div className="card card-b empty" style={{ height: "auto" }}>
              <div className="big">{params.archived ? tr("没有项目") : tr("还没有项目")}</div>
              <div>{tr("一个项目把一批来源文件映射到一个模板并批量生成文档；同结构的新文件可以直接复用。")}</div>
              {!ro && <button type="button" className="btn primary" onClick={create}>{tr("新建第一个项目")}</button>}
            </div>
          )}

          {(!page && loading && !err) && <div className="empty" style={{ height: "auto" }}>{tr("加载项目…")}</div>}

          {page && (page.total > 0 || searching) && (
            <>
              <div className="card" style={{ overflow: "auto" }} aria-busy={loading}>
                <div className="tbl-wrap"><table className="tbl" aria-label={tr("项目列表")} aria-rowcount={page.total + 1}>
                  <thead>
                    <tr><th>{tr("名称")}</th><th>{tr("模板")}</th><th>{tr("来源")}</th><th>{tr("样本规则")}</th><th>{tr("最近任务")}</th><th>{tr("更新")}</th><th><span className="sr-only">{tr("操作")}</span></th></tr>
                  </thead>
                  {/* The tbody is one tab stop for ↑↓ / J K and Enter; each name is also a real
                      link, so Tab, Enter, middle-click and screen-reader link lists all work. */}
                  <tbody ref={listRef} tabIndex={rows.length ? 0 : -1} onKeyDown={onKey} aria-label={tr("项目（↑↓ 移动，回车打开）")}>
                    {rows.map((p, i) => {
                      const [label, tone] = jobStatus(p.last_job?.status);
                      return (
                        <tr key={p.id} data-row aria-rowindex={page.offset + i + 2}
                          className={`rowlink${i === cursor ? " cursor" : ""}${p.archived_at ? " off" : ""}`}
                          onClick={(e) => { if (!(e.target as HTMLElement).closest("a, button")) nav(`/p/${p.id}`); }}>
                          <td>
                            <Link className="projlink" to={`/p/${p.id}`}>{p.name}</Link>
                            {p.archived_at ? <span className="badge" style={{ marginLeft: 6 }}>{tr("已归档")}</span> : null}
                          </td>
                          <td className="small muted">{p.template_name || tr("未设置")}</td>
                          <td className="small">{formatNumber(p.sources)}</td>
                          <td className="small muted nowrap">{ruleMode(p.record_rule?.mode)}</td>
                          <td className="small nowrap">
                            {p.last_job
                              ? <><span className={`badge ${tone}`}>{label}</span>{" "}
                                <span className="muted">{formatNumber(p.last_job.counts?.success || 0)}/{formatNumber(p.last_job.counts?.total || 0)}</span></>
                              : <span className="muted">—</span>}
                          </td>
                          <td className="small muted nowrap">{fmtTime(p.updated_at)}</td>
                          <td className="nowrap">
                            <MoreMenu label="⋯" ariaLabel={tr("项目「{v1}」的操作", { v1: p.name })} items={[
                              { label: tr("打开"), onClick: () => nav(`/p/${p.id}`) },
                              ...(ro ? [] : [
                                { label: tr("重命名…"), onClick: () => rename(p) },
                                { label: p.archived_at ? tr("取消归档") : tr("归档（可撤销）"), onClick: () => archive(p, !p.archived_at) },
                                { label: tr("删除…"), onClick: () => remove(p) },
                                ...(personal ? [{ label: tr("复制到组织工作空间…"), onClick: () => setCopyFor({ id: p.id, name: p.name }) }] : []),
                              ]),
                            ]} />
                          </td>
                        </tr>
                      );
                    })}
                    {!rows.length && (
                      <tr><td colSpan={7} className="muted" style={{ textAlign: "center", padding: 16 }}>{tr("没有匹配「")}{params.q}{tr("」的项目")}</td></tr>
                    )}
                  </tbody>
                </table></div>
              </div>
              <div className="row mt8 wrap tiny muted">
                <span className="grow" aria-live="polite">
                  {rows.length ? tr("第 {v1}–{v2} 个，共 {v3} 个项目", { v1: first, v2: last, v3: page.total }) : tr("共 {v1} 个项目", { v1: page.total })}
                  {loading ? tr(" · 加载中…") : ""}{tr(" · 列表内可用 ↑↓ 移动、回车打开")}</span>
                <button type="button" className="btn sm" disabled={page.offset <= 0 || loading}
                  onClick={() => setParams((p) => ({ ...p, offset: Math.max(0, p.offset - p.limit) }))}>{tr("‹ 上一页")}</button>
                <button type="button" className="btn sm" disabled={page.next_offset == null || loading}
                  onClick={() => setParams((p) => ({ ...p, offset: page.next_offset ?? p.offset }))}>{tr("下一页 ›")}</button>
              </div>
            </>
          )}

          <div className="row mt16 mb8"><h1 className="grow" style={{ marginTop: 24 }}>{tr("模板库")}</h1></div>
          {tplErr && (
            <div className="notice err row mb8" role="alert">
              <span className="grow">{tr("模板库加载失败：")}{serverText(tplErr)}</span>
              <button type="button" className="btn sm" onClick={loadTemplates}>{tr("重试")}</button>
            </div>
          )}
          <div className="card" style={{ overflow: "auto" }}>
            <div className="tbl-wrap"><table className="tbl" aria-label={tr("模板库")}>
              <thead><tr><th>{tr("名称")}</th><th>{tr("格式")}</th><th>{tr("来源")}</th><th>{tr("字段")}</th><th>{tr("更新")}</th><th><span className="sr-only">{tr("操作")}</span></th></tr></thead>
              <tbody>
                {(templates || []).map((t) => (
                  <tr key={t.id}>
                    <td>{t.name}{t.publication && <span className={`badge ${t.publication.deprecated ? "" : "info"}`} style={{ marginLeft: 6 }}>{t.publication.deprecated ? tr("共享已弃用") : tr("已共享 · 第 {v1} 次发布", { v1: t.publication.release_no })}</span>}</td><td>{t.format.toUpperCase()}</td>
                    <td className="small muted">{t.info?.origin ? tr("组织共享模板") : t.kind === "uploaded" ? t.original_name : t.kind === "builtin_doc" ? tr("内置文档编辑器") : tr("内置表格编辑器")}</td>
                    <td>{formatNumber(t.slot_count)}</td><td className="small muted">{fmtTime(t.updated_at)}</td>
                    <td className="nowrap">
                      {!ro && (t.kind === "builtin_doc" || t.kind === "builtin_sheet") && <><Link className="btn xs" to={`/templates/${t.id}/edit`}>{tr("编辑")}</Link>{" "}</>}
                      {!ro && (
                        <MoreMenu label="⋯" ariaLabel={tr("模板「{v1}」的操作", { v1: t.name })} items={[
                          { label: tr("复制"), onClick: async () => { if (await act(() => post(`/api/templates/${t.id}/duplicate`, {}), tr("已复制"))) loadTemplates(); } },
                          { label: tr("重命名…"), onClick: async () => { const n = await prompt(tr("重命名模板"), tr("名称"), t.name); if (n && n !== t.name && await act(() => patch(`/api/templates/${t.id}`, { name: n }))) loadTemplates(); } },
                          { label: tr("删除…"), onClick: async () => { if (await confirm(tr("删除模板"), tr("删除模板「{v1}」？使用它的项目需要重新选择模板。", { v1: t.name }), tr("删除"), true) && await act(() => del(`/api/templates/${t.id}`), tr("已删除模板"))) { loadTemplates(); load(); } } },
                          ...shareItems(t),
                        ]} />
                      )}
                    </td>
                  </tr>
                ))}
                {templates && !templates.length && !tplErr && <tr><td colSpan={6} className="muted" style={{ textAlign: "center", padding: 16 }}>{tr("还没有模板。在项目中上传或新建的模板会出现在这里。")}</td></tr>}
                {!templates && !tplErr && <tr><td colSpan={6} className="muted" style={{ textAlign: "center", padding: 16 }}>{tr("加载模板…")}</td></tr>}
              </tbody>
            </table></div>
          </div>
          {inOrg && (
            <>
              <div className="row mt16 mb8"><h2 className="grow" style={{ marginTop: 16 }}>{tr("组织共享模板")}{shared?.organization ? ` · ${shared.organization.name}` : ""}</h2></div>
              {sharedErr && <div className="notice err row mb8" role="alert"><span className="grow">{tr("组织共享模板加载失败：")}{serverText(sharedErr)}</span><button type="button" className="btn sm" onClick={loadShared}>{tr("重试")}</button></div>}
              <div className="card" style={{ overflow: "auto" }}>
                <div className="tbl-wrap"><table className="tbl" aria-label={tr("组织共享模板")}>
                  <thead><tr><th>{tr("名称")}</th><th>{tr("格式")}</th><th>{tr("字段")}</th><th>{tr("发布")}</th><th>{tr("来源工作空间")}</th><th>{tr("维护者")}</th><th><span className="sr-only">{tr("操作")}</span></th></tr></thead>
                  <tbody>
                    {(shared?.items || []).map((x) => (
                      <tr key={x.id}>
                        <td>{x.name}{x.deprecated && <span className="badge" style={{ marginLeft: 6 }}>{tr("已弃用")}</span>}{x.description && <div className="tiny muted">{x.description}</div>}</td>
                        <td>{String(x.format).toUpperCase()}</td><td>{formatNumber(x.slot_count)}</td>
                        <td className="small muted">{tr("第 {v1} 次发布 · 模板第 {v2} 版", { v1: x.release_no, v2: x.version_no })}<div className="tiny">{fmtTime(x.updated_at)}{x.use_count ? ` · ${tr("已用于 {v1} 个工作空间", { v1: x.use_count })}` : ""}</div></td>
                        <td className="small muted">{x.source_workspace?.name || "—"}</td>
                        <td className="small muted">{(x.updated_by || x.published_by)?.name}</td>
                        <td className="nowrap">
                          {!ro && !x.deprecated && <><button type="button" className="btn xs" onClick={async () => { if (await act(() => post(`/api/organization-templates/${x.id}/use`, {}), tr("已从组织共享模板复制「{v1}」", { v1: x.name }))) { loadTemplates(); loadShared(); } }}>{tr("复制到本工作空间")}</button>{" "}</>}
                          {x.can_manage && !ro && (
                            <MoreMenu label="⋯" ariaLabel={tr("模板「{v1}」的共享操作", { v1: x.name })} items={x.deprecated
                              ? [{ label: tr("恢复共享"), onClick: async () => { if (await act(() => post(`/api/organization-templates/${x.id}/deprecate`, { deprecated: false }), tr("已恢复共享"))) { loadTemplates(); loadShared(); } } }]
                              : [{ label: tr("更新组织发布"), disabled: !x.source_ahead, title: x.source_ahead ? tr("源模板有新版本，可更新发布") : undefined,
                                   onClick: async () => { const r: any = await post(`/api/organization-templates/${x.id}/update`, {}).catch((e) => { toast(errorText(e), "err"); return null; }); if (r) { toast(tr("已发布第 {v1} 次", { v1: r.release_no }), "ok"); loadTemplates(); loadShared(); } } },
                                 { label: tr("弃用共享…"), onClick: async () => { if (await confirm(tr("弃用共享模板"), tr("弃用后组织成员不能再用它建立新项目；已复制到各工作空间的模板与任务不受影响。"), tr("弃用"), true) && await act(() => post(`/api/organization-templates/${x.id}/deprecate`, {}), tr("已弃用"))) { loadTemplates(); loadShared(); } } }]} />
                          )}
                        </td>
                      </tr>
                    ))}
                    {shared && !shared.items.length && !sharedErr && <tr><td colSpan={7} className="muted" style={{ textAlign: "center", padding: 16 }}>{tr("组织里还没有共享模板。在模板库对模板选择「发布到组织…」。")}</td></tr>}
                    {!shared && !sharedErr && <tr><td colSpan={7} className="muted" style={{ textAlign: "center", padding: 16 }}>{tr("加载模板…")}</td></tr>}
                  </tbody>
                </table></div>
              </div>
            </>
          )}
          <div className="tiny muted mt16">{tr("Synwevo Docs 的解析、转换、文字识别与生成都在本服务的服务器上完成。只有使用个人 Agent 时，对话与你选择发送的文件内容会发送给所配置的模型提供方。")}</div>
        </div>
      </div>
      {copyFor && <CopyRequestDialog project={copyFor} onClose={() => { setCopyFor(null); load(); }} />}
    </div>
  );
}
