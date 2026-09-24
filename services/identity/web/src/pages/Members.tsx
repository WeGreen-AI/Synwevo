/**
 * Members and invitations of one workspace, for its owners and admins.
 *
 * Lives in the account shell because the routes it calls are served only by the account service
 * (roles `accounts` and `all`); the portal and the applications refuse them by role.
 *
 * What the page may do is the server's decision. A viewer or editor who opens the address gets
 * the server's 403 rendered as such, a non-member gets its 404 — nothing here hides a control
 * as a substitute for the server refusing it. Where the page does disable something in advance
 * (a promotion in a frozen workspace, acting on someone above your own role), it says why.
 */
import { KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ApiError, get, qs, useSession } from "@synwevo/auth-client";
import { isImeKey } from "@synwevo/ui";
import { Topbar } from "../components/Topbar";
import { useDebounced, useLoad } from "../lib/hooks";
import { errText, roleLabel, WORKSPACE_KIND, WORKSPACE_STATUS } from "../lib/labels";
import type { InvitationList, MemberPage } from "./members/types";
import { MembersPanel, type Filters } from "./members/MembersPanel";
import { InvitesPanel } from "./members/InvitesPanel";

const PAGE = 50;
type Tab = "members" | "invites";

export default function Members() {
  const { wid = "" } = useParams();
  return <WorkspaceMembers key={wid} wid={wid} />;
}

function WorkspaceMembers({ wid }: { wid: string }) {
  const { me } = useSession();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get("tab") === "invites" ? "invites" : "members";
  const setTab = (t: Tab) => setParams(t === "invites" ? { tab: "invites" } : {}, { replace: true });

  const [filters, setFilters] = useState<Filters>({ q: "", role: "", status: "", entitlement: "" });
  const [offset, setOffset] = useState(0);
  const q = useDebounced(filters.q.trim(), 300);
  const memberKey = `${wid}|${q}|${filters.role}|${filters.status}|${filters.entitlement}|${offset}`;
  const members = useLoad<MemberPage>(memberKey, (signal) => get<MemberPage>(
    `/api/auth/workspaces/${encodeURIComponent(wid)}/members${qs({
      q, role: filters.role, status: filters.status, entitlement: filters.entitlement, offset, limit: PAGE,
    })}`, signal));
  const invites = useLoad<InvitationList>(`${wid}|invites`, (signal) => get<InvitationList>(
    `/api/auth/workspaces/${encodeURIComponent(wid)}/invitations`, signal));

  // A new filter starts from the first page; staying on page 3 of a narrower result shows nothing.
  const setFiltersFromStart = useCallback((f: Filters) => { setFilters(f); setOffset(0); }, []);

  // Screen-reader announcements for things that change without moving focus.
  const [said, setSaid] = useState("");
  const announce = useCallback((t: string) => { setSaid(""); requestAnimationFrame(() => setSaid(t)); }, []);

  const page = members.data;
  const ws = page?.workspace;
  const governed = (me?.workspaces || []).filter((w) => w.role === "owner" || w.role === "admin");
  const title = ws ? `成员与邀请 · ${ws.name}` : "成员与邀请";
  useEffect(() => { document.title = `${title} · Synwevo Account`; }, [title]);

  const denied = members.error instanceof ApiError && !members.error.upstream
    && (members.error.status === 403 || members.error.status === 404) ? members.error : null;

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: "members", label: "成员", count: page?.total },
    { id: "invites", label: "邀请", count: invites.data?.groups.pending },
  ];
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const onTabKey = (e: KeyboardEvent) => {
    if (isImeKey(e) || (e.key !== "ArrowRight" && e.key !== "ArrowLeft")) return;
    e.preventDefault();
    const next: Tab = tab === "members" ? "invites" : "members";
    setTab(next);
    tabRefs.current[next]?.focus();
  };

  return (
    <div className="app">
      <Topbar title="成员与邀请">
        {governed.length > 1 && (
          <select className="input sm" style={{ maxWidth: 200 }} aria-label="切换要管理的工作空间"
            value={governed.some((w) => w.id === wid) ? wid : ""}
            onChange={(e) => { if (e.target.value) nav(`/account/workspaces/${encodeURIComponent(e.target.value)}/members`); }}>
            {!governed.some((w) => w.id === wid) && <option value="">选择工作空间…</option>}
            {governed.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        )}
        <a className="btn ghost sm" href="/account">账户与安全</a>
      </Topbar>

      <main style={{ overflow: "auto", flex: 1 }}>
        <div className="acct-page">
          {denied ? (
            <div className="empty" role="alert">
              <div className="big">{denied.status === 404 ? "找不到这个工作空间" : "你不能管理这个工作空间的成员"}</div>
              <div>
                {denied.status === 404
                  ? "它不存在，或者你不是它的成员。"
                  : `只有空间所有者和管理员可以查看与管理成员。${(() => {
                    const mine = me?.workspaces.find((w) => w.id === wid);
                    return mine ? `你在这里的角色是「${roleLabel(mine.role)}」。` : "";
                  })()}`}
              </div>
              <a className="btn" href="/account">返回账户与安全</a>
            </div>
          ) : !page && members.error ? (
            <div className="empty" role="alert">
              <div className="big">成员列表加载失败</div>
              <div>{errText(members.error)}</div>
              <button type="button" className="btn primary" onClick={members.reload}>重试</button>
            </div>
          ) : !page ? (
            <div className="empty" role="status">加载中…</div>
          ) : (
            <>
              <div className="acct-head">
                <div className="grow">
                  <h1>{page.workspace.name}</h1>
                  <div className="sub statline">
                    <span>{WORKSPACE_KIND[page.workspace.kind] || page.workspace.kind}空间</span>
                    <span aria-hidden="true">·</span>
                    <span className={`badge ${page.workspace.status === "active" ? "ok" : "warn"}`}>
                      {WORKSPACE_STATUS[page.workspace.status] || page.workspace.status}
                    </span>
                    <span aria-hidden="true">·</span>
                    <span>你的角色：{roleLabel(page.caller.role)}</span>
                    {page.app && <>
                      <span aria-hidden="true">·</span>
                      <span>{page.app.name}：{page.app.access_policy === "members" ? "使用资格随成员关系，可逐人撤销" : "需要逐人授予使用资格"}</span>
                    </>}
                  </div>
                </div>
              </div>

              {page.app && <p className="small muted mt12">实际访问仍取决于账户、空间和应用是否可用；授予使用资格不会启用已停用的应用。</p>}

              {page.workspace.status === "frozen" && (
                <div className="notice warn mt12" role="note">
                  这个空间已被平台管理员冻结：成员暂时无法使用其中的应用。你仍然可以移除成员、撤销权限，
                  但不能邀请新成员、提升角色或授予权限，直到空间解冻。
                </div>
              )}
              {page.single_owner && page.workspace.kind !== "personal" && (
                <div className="notice mt12" role="note">
                  这个空间只有 <b>{page.active_owner_count}</b> 位状态正常的所有者。建议再指定一位所有者，
                  以免唯一的所有者无法联系时没有人能管理这个空间。
                </div>
              )}

              <div className="acct-tabs" role="tablist" aria-label="成员与邀请" onKeyDown={onTabKey}>
                {tabs.map((t) => (
                  <button key={t.id} type="button" role="tab" id={`tab-${t.id}`} aria-controls={`panel-${t.id}`}
                    aria-selected={tab === t.id} tabIndex={tab === t.id ? 0 : -1}
                    ref={(el) => { tabRefs.current[t.id] = el; }}
                    onClick={() => setTab(t.id)}>
                    {t.label}
                    {t.count !== undefined && <span className="count">{t.id === "invites" ? `${t.count} 待接受` : t.count}</span>}
                  </button>
                ))}
              </div>

              <section role="tabpanel" id="panel-members" aria-labelledby="tab-members" hidden={tab !== "members"}>
                <MembersPanel wid={wid} page={page} loading={members.loading} error={members.error}
                  filters={filters} setFilters={setFiltersFromStart} offset={offset} setOffset={setOffset}
                  reload={members.reload} announce={announce} onInvitesChanged={invites.reload} />
              </section>
              <section role="tabpanel" id="panel-invites" aria-labelledby="tab-invites" hidden={tab !== "invites"}>
                <InvitesPanel wid={wid} ws={page.workspace} callerRole={page.caller.role}
                  list={invites.data} loading={invites.loading} error={invites.error} reload={invites.reload}
                  announce={announce} />
              </section>
            </>
          )}
        </div>
      </main>
      <div className="sr-only" role="status" aria-live="polite">{said}</div>
    </div>
  );
}
