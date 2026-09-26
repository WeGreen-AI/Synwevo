import { formatNumber, LanguageMenu, BrandMark, PageState, useMediaQuery } from "@synwevo/ui";
import { DownloadLink } from "../components/ApiResource";
import { serverText, issueText, diagnosticText } from "../lib/presentation";
import { t as tr, useI18n } from "@synwevo/ui";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, del, get, patch, post, put, qs, useSession, workspaceContext } from "@synwevo/auth-client";
import { initialState, openRoom, type Focus, type Room, type RoomState } from "../lib/collab";
import ProjectPresence, { ActivityDrawer, changeText } from "../components/ProjectPresence";
import { describeSelection, mappingSlots, slotPos } from "../lib/describe";
import CommentsDrawer, { type AnchorOption, type CommentAnchor } from "../components/CommentsDrawer";
import type { Mapping, Mark, Project, RecordRule, Selection, Slot, SourceFile, TemplateDetail } from "../lib/types";
import { anyModalOpen, isImeKey, Modal, useDialog, useToast } from "@synwevo/ui";
import { recall, remember } from "@synwevo/ui";
import { jobStatus, ruleMode } from "../lib/status";
import { errorText, isPermissionError, NO_EXPORT_TEXT, permissions, READ_ONLY_TEXT } from "../lib/perm";
import { markColor, FIND_MARK } from "../lib/css";
import AnyView from "../components/AnyView";
import CommandPalette, { Command } from "../components/CommandPalette";
import Shortcuts from "../components/Shortcuts";
import Connectors from "../components/Connectors";
import FilePanel from "../components/FilePanel";
import GenerateDialog from "../components/GenerateDialog";
import TemplateVersionsDialog from "../components/TemplateVersionsDialog";
import MappingDialog, { Proposal } from "../components/MappingDialog";
import PreviewPane from "../components/PreviewPane";
import RegressionDialog from "../components/RegressionDialog";
import DesensitizeDialog from "../components/DesensitizeDialog";
import MoreMenu from "../components/MoreMenu";
import RuleDialog from "../components/RuleDialog";
import SidePanel, { SideTab } from "../components/SidePanel";
import SlotDialog, { suggestName } from "../components/SlotDialog";
import TemplateChooser from "../components/TemplateChooser";

/** 409 from a save or an undo/redo decided on a state that has moved on (another tab, another
 *  member): mappings answer `stale`, the template `stale_template`. */
const isStale = (e: unknown) => {
  const code = (e as { code?: string } | null)?.code;
  return code === "stale" || code === "stale_template" || code === "stale_project";
};
const STALE_RELOADED = "内容已在其他窗口或被其他人修改，这次操作没有执行。已刷新为最新内容，请在此基础上重新操作。";

type Dialog =
  | null
  | { kind: "rule"; initial?: Partial<RecordRule> }
  | { kind: "template" }
  | { kind: "slot"; sel: Selection; name: string }
  | { kind: "mapping"; slot: Slot; proposals: Proposal[]; warnings: string[]; existing: Mapping | null; editing: Mapping | null }
  | { kind: "generate" }
  | { kind: "versions" }
  | { kind: "area"; sel: Selection & { kind: "cells" } }
  | { kind: "regression" }
  | { kind: "desensitize" };


/** Which panel a phone shows: the source file, the target template (or preview), or the mapping list. */
type MobilePane = "files" | "source" | "target" | "map";

export default function WorkspaceRoute() {
  useI18n();
  const { pid } = useParams();
  return <Workspace key={pid} />;
}

function Workspace() {
  const { locale } = useI18n();
  const { pid } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const { confirm } = useDialog();
  const session = useSession();
  // What this member may do here. The server enforces it (403 read_only_role /
  // export_not_allowed); the UI only avoids offering actions that can only be refused.
  const perm = permissions(session.me, session.disabled);
  const ro = !perm.canWrite;
  const [project, setProject] = useState<Project | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const loaded = useRef(false);          // "the workspace is on screen" — read inside reload's closure
  const [sources, setSources] = useState<SourceFile[]>([]);
  const [activeFile, setActiveFile] = useState<string | null>(null);
  const [srcView, setSrcView] = useState<any>(null);
  // 「每块一份」: which form block of the active file is shown on the left
  const [blocks, setBlocks] = useState<{ index: number; label: string; where: string }[]>([]);
  const [blockIdx, setBlockIdx] = useState<number | null>(null);
  const [srcSheet, setSrcSheet] = useState<string | null>(null);
  const [srcPage, setSrcPage] = useState(0);
  const [tplView, setTplView] = useState<any>(null);
  const [tplSheet, setTplSheet] = useState<string | null>(null);
  const [tplPage, setTplPage] = useState(0);
  const [leftSel, setLeftSel] = useState<Selection | null>(null);
  const [rightSel, setRightSel] = useState<Selection | null>(null);
  const [activeMap, setActiveMap] = useState<string | null>(null);
  const [locate, setLocate] = useState<Record<string, any[]>>({});
  const [save, setSave] = useState<"saved" | "saving" | "error">("saved");
  const [dialog, setDialog] = useState<Dialog>(null);
  const [tab, setTab] = useState<SideTab>(() => recall<SideTab>("tab", "mappings"));
  const [preview, setPreview] = useState(false);
  const [lines, setLines] = useState(() => recall("lines", true));
  const [leftW, setLeftW] = useState<number>(() => recall("leftW", 0));
  const [mobilePane, setMobilePane] = useState<MobilePane>("source");
  const narrow = useMediaQuery("(max-width: 900px)");
  const [zoomL, setZoomL] = useState(() => recall("zoomL", 1));
  const [zoomR, setZoomR] = useState(() => recall("zoomR", 1));
  const [sideW, setSideW] = useState(() => recall("sideW", 0));
  const [cmdOpen, setCmdOpen] = useState(false);
  const [help, setHelp] = useState(false);
  // ---- the project room: who is here and on what, and other people's saves as they happen
  const roomRef = useRef<Room | null>(null);
  if (pid && !roomRef.current) roomRef.current = openRoom(pid, { origin: window.location.origin, workspaceId: workspaceContext(), kind: "project" });
  const [room, setRoom] = useState<RoomState>(initialState());
  const [activityOpen, setActivityOpen] = useState(false);
  const [comments, setComments] = useState<string | null | false>(() => { const c = new URLSearchParams(window.location.search).get("comment"); return c ? c : false; });
  const [focusL, setFocusL] = useState<string | null>(null);
  const [focusR, setFocusR] = useState<string | null>(null);
  const [gridFocusL, setGridFocusL] = useState<any>(null);
  const [gridFocusR, setGridFocusR] = useState<any>(null);
  const [filesCollapsed, setFilesCollapsed] = useState(() => recall("filesCollapsed", false));
  const [sideCollapsed, setSideCollapsed] = useState(() => recall("sideCollapsed", false));
  const [relocate, setRelocate] = useState<{ fileId: string; mappingId: string } | null>(null);
  const [tick, setTick] = useState(0);
  const [guide, setGuide] = useState(() => recall("guide", true));
  const [singleKeys, setSingleKeys] = useState(() => recall("singleKeys", true));
  const [uploadReq, setUploadReq] = useState(0);
  const [ocrBusy, setOcrBusy] = useState(false);
  const [findQ, setFindQ] = useState("");
  const findRequest = useRef<AbortController | null>(null);
  useEffect(() => () => findRequest.current?.abort(), []);
  const [find, setFind] = useState<{ hits: any[]; total: number; idx: number; q: string } | null>(null);
  const actions = useRef<{ undo: ("m" | "t")[]; redo: ("m" | "t")[] }>({ undo: [], redo: [] });
  const workRef = useRef<HTMLDivElement>(null);
  const findRef = useRef<HTMLInputElement>(null);

  // remembered layout – the workspace comes back the way it was left
  useEffect(() => { remember("lines", lines); }, [lines]);
  useEffect(() => { remember("tab", tab); }, [tab]);
  useEffect(() => { remember("zoomL", zoomL); }, [zoomL]);
  useEffect(() => { remember("zoomR", zoomR); }, [zoomR]);
  useEffect(() => { remember("filesCollapsed", filesCollapsed); }, [filesCollapsed]);
  useEffect(() => { remember("sideCollapsed", sideCollapsed); }, [sideCollapsed]);
  useEffect(() => { remember("sideW", sideW); }, [sideW]);
  useEffect(() => { remember("guide", guide); }, [guide]);
  useEffect(() => { remember("singleKeys", singleKeys); }, [singleKeys]);

  const tpl: TemplateDetail | null = project?.template || null;
  const slots: Slot[] = tpl?.version.slots || [];
  const mappings: Mapping[] = project?.mappings.mappings || [];
  const rule = project?.record_rule || {};
  const file = sources.find((s) => s.id === activeFile) || null;

  // ------------------------------------------------------------------ loading
  const pendingLocate = useRef<{ kind: "slot" | "mapping" | "source"; id: string } | null>(null);
  const reload = useCallback(async () => {
    try {
      const p: Project = await get(`/api/projects/${pid}`);
      setProject(p);
      setSources(p.sources);
      setLoadErr(null);
      loaded.current = true;
      return p;
    } catch (e: any) {
      // Once the workspace is on screen it must not be discarded over a failed refresh. The
      // early return further down replaces the whole editor — template pane, mappings,
      // connectors, everything the user was working on — with an error page and a link home,
      // and one CDN rate-limit on the 900ms parse-poll was enough to trigger it.
      const transient = e instanceof ApiError && (e.upstream || e.status === 0 || e.status >= 500);
      if (loaded.current && transient) toast(errorText(e), "err");
      else setLoadErr(errorText(e));
      return null;
    }
  }, [pid, toast]);

  useEffect(() => {
    const r = roomRef.current;
    if (!r) return;
    const off = r.subscribe(setRoom);
    return () => { off(); r.destroy(); roomRef.current = null; };
  }, []);
  useEffect(() => {
    if (!roomRef.current || !room.me) return;
    roomRef.current.setUser({ ...room.me, slot: room.me.color, readonly: room.readonly } as any);
  }, [room.me, room.readonly]);
  // where we are, as a stable object: the selected field, the active mapping, or the open file
  useEffect(() => {
    const focus: Focus | null = rightSel?.kind === "slot" ? { kind: "slot", id: rightSel.slotId }
      : activeMap ? { kind: "mapping", id: activeMap }
      : activeFile ? { kind: "source", id: activeFile } : null;
    roomRef.current?.setFocus(focus);
  }, [rightSel, activeMap, activeFile, room.me]);
  // someone else changed the project: show it now, and say who did what
  useEffect(() => {
    const c = room.lastChange;
    if (!c || !room.me || (c.by && c.by.id === room.me.id)) return;
    void reload();
    toast(tr("{v1} {v2}", { v1: c.by?.name || tr("成员"), v2: changeText(c.kind, c.detail, tpl?.version.slots || []) }), "info");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [room.lastChange?.seq]);

  useEffect(() => {
    reload().then((p) => {
      if (p && !activeFile) {
        const rep = p.sources.find((s) => s.representative) || p.sources[0];
        if (rep) setActiveFile(rep.id);
      }
      // a deep link from a job's failed sample (?locate=slot:<id> | mapping:<id> | source:<id>):
      // put that object in front of the person once, then drop the parameter from the address
      const q = new URLSearchParams(window.location.search);
      const want = q.get("locate");
      const m = p && want ? /^(slot|mapping|source):([A-Za-z0-9_-]{1,64})$/.exec(want) : null;
      if (m && p) {
        const target = { kind: m[1] as "slot" | "mapping" | "source", id: m[2] };
        // a field selection is applied after the template has loaded: loading it resets the
        // right-hand selection (the effect on tpl?.id), which would wipe a selection made now
        const gone = (target.kind === "mapping" && !(p.mappings?.mappings || []).some((mm) => mm.id === target.id))
          || (target.kind === "source" && !p.sources.some((sf) => sf.id === target.id))
          || (target.kind === "slot" && !p.template);
        if (gone) toast(tr("链接指向的{v1}已不存在", { v1: { slot: tr("字段"), mapping: tr("映射"), source: tr("文件") }[target.kind] }), "info");
        else if (target.kind === "slot") pendingLocate.current = target;
        else locateComment(target);
        q.delete("locate");
        const rest = q.toString();
        window.history.replaceState(window.history.state, "", window.location.pathname + (rest ? `?${rest}` : "") + window.location.hash);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  // poll while files are parsing
  useEffect(() => {
    if (!sources.some((s) => s.status === "parsing")) return;
    let alive = true;
    let t: any = setTimeout(async function tick() {
      const p = await reload();
      // A refresh that failed for transport reasons says nothing about whether parsing has
      // finished, so keep asking. On success setSources re-runs this effect, and the `alive`
      // guard stops the old tick from scheduling a second timer alongside the new one.
      if (alive && !p) t = setTimeout(tick, 900);
    }, 900);
    return () => { alive = false; clearTimeout(t); };
  }, [sources, reload]);

  useEffect(() => {
    if (!activeFile && sources.length) setActiveFile((sources.find((s) => s.representative) || sources[0]).id);
    if (activeFile && !sources.find((s) => s.id === activeFile)) setActiveFile(sources[0]?.id || null);
  }, [sources, activeFile]);

  const blockMode = project?.record_rule?.mode === "per_block";
  const blockRuleKey = blockMode ? JSON.stringify(project?.record_rule?.block || {}) : "";
  useEffect(() => {
    if (!file || !blockMode || file.status === "parsing") {
      setBlocks([]);
      setBlockIdx(null);
      return;
    }
    // Every fetch below is tied to the view it was made for: a slow answer for the previous file
    // (a large XLSX next to a small CSV) must not land after the current one and be mistaken for
    // it — selections made on that view become locators for the wrong file.
    let alive = true;
    get(`/api/sources/${file.id}/blocks`).then((r) => {
      if (!alive) return;
      setBlocks(r.blocks || []);
      setBlockIdx((cur) => (r.blocks?.length ? (cur != null && cur < r.blocks.length ? cur : 0) : null));
      if (r.error) toast(diagnosticText(r.error));
    }).catch(() => { if (alive) { setBlocks([]); setBlockIdx(null); } });
    return () => { alive = false; };
  }, [file?.id, file?.version, file?.status, blockMode, blockRuleKey]);
  const bq = blockIdx != null ? { block: blockIdx } : {};
  const bAmp = blockIdx != null ? `&block=${blockIdx}` : "";

  // source view (non-grid parts are rendered from this response; grids page separately)
  const srcKey = file ? `${file.id}:${file.version}:${file.status}:${blockIdx ?? ""}` : "";
  useEffect(() => {
    findRequest.current?.abort();
    setSrcView(null);
    setLeftSel(null); // never keep a selection from another file
    setFind(null);
    setSrcSheet(null);
    setSrcPage(0);
    if (!file) return;
    if (blockMode && blockIdx == null) return;   // wait for the block list
    let alive = true;
    get(`/api/sources/${file.id}/view${qs({ limit: 1, ...bq })}`)
      .then((v) => alive && setSrcView(v))
      .catch((e) => alive && setSrcView({ view: "error", error: errorText(e) }));
    return () => { alive = false; };
  }, [srcKey]);

  const tplKey = tpl ? `${tpl.id}:${tpl.head}` : "";
  useEffect(() => {
    setRightSel(null);
    setTplSheet(null);
    setTplPage(0);
    setGridFocusR(null);
    if (tpl && pendingLocate.current) {          // a deep link's field, once the template is here
      const target = pendingLocate.current;
      pendingLocate.current = null;
      if (tpl.version.slots.some((sl) => sl.id === target.id)) locateComment(target);
      else toast(tr("链接指向的{v1}已不存在", { v1: tr("字段") }), "info");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tpl?.id]);
  useEffect(() => {
    setTplView(null);
    if (!tpl) return;
    let alive = true;
    get(`/api/templates/${tpl.id}/view${qs({ limit: 1 })}`)
      .then((v) => alive && setTplView(v))
      .catch((e) => alive && setTplView({ view: "error", error: errorText(e) }));
    return () => { alive = false; };
  }, [tplKey]);

  // where each mapping resolves in the active file
  useEffect(() => {
    if (!file || !mappings.length || file.status === "parsing") {
      setLocate({});
      return;
    }
    if (blockMode && blockIdx == null) return;
    let alive = true;
    get(`/api/projects/${pid}/locate${qs({ file: file.id, ...bq })}`)
      .then((l) => alive && setLocate(l))
      .catch(() => alive && setLocate({}));
    return () => { alive = false; };
  }, [srcKey, project?.rev]);

  // ------------------------------------------------------------------ persistence helpers
  const run = async <T,>(fn: () => Promise<T>, domain?: "m" | "t"): Promise<T | undefined> => {
    setSave("saving");
    try {
      const r = await fn();
      setSave("saved");
      if (domain) {
        actions.current.undo.push(domain);
        actions.current.redo = [];
      }
      return r;
    } catch (e: any) {
      // A 4xx from Synwevo Docs means the server rejected this edit but holds a valid state of its
      // own, so the badge stays 已自动保存 and reload() resyncs. That reasoning does not extend
      // to a refusal the server never made: an intermediary's 403 and the auth gate's 401 both
      // mean the save did not arrive. The toast that says so disappears after 8s, and a stale
      // green ✓ is what the user is left looking at when they close the tab.
      const serverHasIt = e instanceof ApiError && !e.upstream && e.status > 401 && e.status < 500;
      setSave(serverHasIt ? "saved" : "error");
      if (isStale(e)) {
        // Someone else's edit landed first; this screen is behind. Show the current state
        // instead of leaving it to the person to find the reload button.
        toast(STALE_RELOADED, "err");
        void reload();
      } else {
        // A viewer's edit refused with read_only_role says who can change that, not「请求失败」.
        toast(errorText(e), isPermissionError(e) ? "info" : "err");
      }
      throw e;
    }
  };

  /** Save the mapping list. Someone else's save since this list was loaded is merged mapping by
   *  mapping on the server; only a mapping both sides changed comes back as a conflict, and the
   *  person then chooses their version (sent again with `force_ids`) or theirs (reload). */
  const saveMappings = async (ms: Mapping[], note: string) => {
    const base = project?.mappings.rev;
    await run(async () => {
      try {
        return await put(`/api/projects/${pid}/mappings`, { mappings: ms, note, base_rev: base });
      } catch (e: any) {
        const conflicts: { id: string; no: number; reason: string }[] | undefined = isStale(e) ? e.detail?.conflicts : undefined;
        if (!conflicts?.length) throw e;
        const reasons: Record<string, string> = { both_changed: tr("双方都改了它"), deleted_by_other: tr("对方已删除它"), deleted_by_you: tr("你删除了它，对方改了它"), both_added: tr("双方各自新增") };
        const list = conflicts.map((c) => tr("映射 #{v1}：{v2}", { v1: c.no, v2: reasons[c.reason] || c.reason })).join("；");
        const mine = await confirm(tr("映射有冲突"), tr("其他成员在你之后也保存了映射。没有冲突的修改已可合并；下面这些映射双方都动过，请选择保留哪一方：{v1}", { v1: list }), tr("以我的为准"), true);
        if (!mine) { toast(tr("已改为对方的版本；你对这些映射的修改未保存"), "info"); await reload(); return undefined; }
        return await put(`/api/projects/${pid}/mappings`, { mappings: ms, note, base_rev: base, force_ids: conflicts.map((c) => c.id) });
      }
    }, "m");
    await reload();
  };

  /** One undo/redo step of either stack. Every entry point (⌘Z, the ↶/↷ buttons above the
   *  template and the mapping list, the palette) goes through here, so a refusal is reported
   *  instead of an unhandled rejection that left the buttons looking as if they had worked. */
  const step = async (dir: "undo" | "redo", d: "m" | "t") => {
    try {
      // The revision this step was decided on travels with it: when someone else changed the
      // mappings or the template meanwhile, the server refuses with 409 (stale / stale_template)
      // instead of undoing their edit.
      if (d === "m") await post(`/api/projects/${pid}/mappings/${dir}`, { base_rev: project?.mappings.rev });
      else await post(`/api/templates/${tpl!.id}/${dir}`, { head: tpl?.head });
      (dir === "undo" ? actions.current.redo : actions.current.undo).push(d);
      if (dir === "undo") toast(d === "m" ? tr("已撤销映射修改") : tr("已撤销模板修改"));
      await reload();
      return true;
    } catch (e: any) {
      if (isStale(e)) {
        toast(STALE_RELOADED, "err");
        await reload();
      } else if (e?.code === "live_edits") {
        // The editing room holds edits nobody saved as a version yet. A keyboard undo is not the
        // place to throw them away: say who, and point at the one deliberate path (restore).
        const who = ((e.detail || {}).online as string[] | undefined) || [];
        toast(who.length ? tr("编辑器里有尚未保存为版本的实时修改（{v1}），这一步没有执行。要放弃这些修改，请在模板版本历史里恢复版本。", { v1: who.join("、") })
          : tr("编辑器里有尚未保存为版本的实时修改，这一步没有执行。要放弃这些修改，请在模板版本历史里恢复版本。"), "err");
      } else {
        toast(errorText(e), isPermissionError(e) ? "info" : "err");
      }
      return false;
    }
  };
  const undo = async () => {
    const d = actions.current.undo.pop() || (project?.mappings.can_undo ? "m" : tpl?.can_undo ? "t" : undefined);
    if (d) await step("undo", d);
  };
  const redo = async () => {
    const d = actions.current.redo.pop() || (project?.mappings.can_redo ? "m" : tpl?.can_redo ? "t" : undefined);
    if (d) await step("redo", d);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isImeKey(e)) return;   // never steal keys from an IME
      const t = e.target as HTMLElement;
      const typing = !!t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
      const mod = e.metaKey || e.ctrlKey;
      // the palette works from anywhere, including while typing in a field — but never over a
      // dialog: its commands replace the dialog, and its Escape used to close both.
      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (cmdOpen) setCmdOpen(false);
        else if (!dialog && !help && !anyModalOpen()) setCmdOpen(true);
        return;
      }
      // Anything else while a dialog (ours, a confirm, the palette) is open belongs to that
      // dialog: ⌘Z must not undo an edit on the page behind a question still being asked.
      if (cmdOpen || help || dialog || anyModalOpen()) return;
      if (mod && e.key.toLowerCase() === "f" && file && file.status !== "parsing" && !preview) {
        e.preventDefault();                                       // replace the browser find: ours searches the parsed file
        findRef.current?.focus();
        findRef.current?.select();
        return;
      }
      if (typing) return;
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (!ro) e.shiftKey ? redo() : undo();       // a viewer has nothing to undo
      } else if (mod && e.key.toLowerCase() === "y") {
        e.preventDefault();
        if (!ro) redo();
      } else if (mod && e.key === "Enter") {
        if (tpl && rule.mode && !ro) { e.preventDefault(); setDialog({ kind: "generate" }); }
      } else if (singleKeys && !mod && !e.altKey && (e.key === "?" || (e.key === "/" && e.shiftKey))) {
        // Single-character shortcuts can be switched off in the shortcut help (WCAG 2.1.4).
        e.preventDefault();
        setHelp(true);
      } else if (singleKeys && e.key === "p" && !mod && !e.altKey) {
        if (tpl && rule.mode) { e.preventDefault(); setPreview((v) => !v); }
      } else if (e.key === "Escape") {
        setLeftSel(null);
        setRightSel(null);
        setActiveMap(null);
        setRelocate(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // ------------------------------------------------------------------ marks
  const slotMapping = (s: Slot) => mappings.find((m) => (m.target.binding || m.target.slot_id) === (s.binding || s.id));
  const leftMarks: Mark[] = useMemo(() => {
    const out: Mark[] = [];
    const fh = find?.hits[find.idx];
    if (fh) {
      const pos: any = fh.kind === "cells" ? { kind: "cells", sheet: fh.sheet, r1: fh.r, c1: fh.c, r2: fh.r, c2: fh.c } : fh.kind === "pptx" ? { kind: "pptx", slide: fh.slide, shape: fh.shape } : fh;
      out.push({ id: "__find", color: FIND_MARK, role: "src", pos, active: true, label: tr("查找结果：{v1}", { v1: fh.text }) });
    }
    for (const m of mappings) {
      (locate[m.id] || []).forEach((r, i) => {
        if (!r.ok || !r.pos) return;
        out.push({ id: i ? `${m.id}~${i}` : m.id, no: m.no, color: markColor(m.color), role: "src", pos: r.pos, active: m.id === activeMap, dim: !m.enabled, label: tr("映射 #{v1}", { v1: m.no }) });
      });
    }
    return out;
  }, [mappings, locate, activeMap, find, locale]);

  const rightMarks: Mark[] = useMemo(() => {
    if (!tpl) return [];
    const out: Mark[] = [];
    for (const s of slots) {
      const pos = slotPos(s, tpl.format);
      if (!pos) continue;
      const m = slotMapping(s);
      out.push({ id: m ? m.id : s.id, no: m?.no, color: markColor(m?.color), role: m ? "tgt" : "slot", pos, active: (m && m.id === activeMap) || (rightSel?.kind === "slot" && rightSel.slotId === s.id), label: tr("字段「{v1}」", { v1: s.name }) + (m ? tr("（映射 #{v1}）", { v1: m.no }) : tr("（未映射）")) });
    }
    return out;
  }, [slots, mappings, activeMap, rightSel, tpl, locale]);

  const slotNames = useMemo(() => {
    const o: Record<string, { name: string; color: string; no?: number; cols?: Record<string, string> }> = {};
    for (const s of slots) {
      const m = slotMapping(s);
      o[s.id] = { name: s.name, color: markColor(m?.color), no: m?.no, cols: Object.fromEntries((s.columns || []).map((c) => [c.key, c.name])) };
    }
    return o;
  }, [slots, mappings]);

  // ------------------------------------------------------------------ interactions
  const pickMapping = (id: string) => {
    setActiveMap(id);
    const m = mappings.find((x) => x.id === id);
    if (!m) return;
    // scroll both sides
    const loc = (locate[id] || [])[0];
    if (loc?.pos?.kind === "cells") {
      if (srcSheet !== loc.pos.sheet) setSrcSheet(loc.pos.sheet);
      setGridFocusL({ sheet: loc.pos.sheet, r: loc.pos.r1, c: loc.pos.c1, key: Date.now() });
    } else if (loc?.pos?.kind === "rect") setSrcPage(loc.pos.page);
    setFocusL(`src:${id}`);
    const s = mappingSlots(m, slots)[0];
    if (s && tpl) {
      const pos = slotPos(s, tpl.format) as any;
      if (pos?.kind === "cells") {
        if (tplSheet !== pos.sheet) setTplSheet(pos.sheet);
        setGridFocusR({ sheet: pos.sheet, r: pos.r1, c: pos.c1, key: Date.now() });
      } else if (pos?.kind === "rect") setTplPage(pos.page);
      setFocusR(`tgt:${id}`);
      setRightSel({ kind: "slot", slotId: s.id });
    }
    setTimeout(() => { setFocusL(null); setFocusR(null); }, 50);
    setTick((t) => t + 1);
  };

  const gotoFind = (f: { hits: any[]; total: number; idx: number; q: string }) => {
    setFind(f);
    const h = f.hits[f.idx];
    if (!h) return;
    if (h.kind === "cells") {
      if (srcSheet !== h.sheet) setSrcSheet(h.sheet);
      setTimeout(() => setGridFocusL({ sheet: h.sheet, r: h.r, c: h.c, key: Date.now() }), 60);
    } else if (h.kind === "rect") setSrcPage(h.page);
    setTimeout(() => { setFocusL("src:__find"); setTimeout(() => setFocusL(null), 50); }, 120);
  };
  const runFind = async () => {
    findRequest.current?.abort();
    if (!file || !findQ.trim()) { setFind(null); return; }
    const ctl = new AbortController();
    findRequest.current = ctl;
    try {
      const r = await get(`/api/sources/${file.id}/search${qs({ q: findQ.trim(), ...bq })}`, ctl.signal);
      if (ctl.signal.aborted) return;
      if (!r.hits.length) { toast(tr("「{v1}」没有找到", { v1: findQ })); setFind({ hits: [], total: 0, idx: 0, q: findQ.trim() }); return; }
      gotoFind({ ...r, idx: 0, q: findQ.trim() });
    } catch (e: any) {
      if (ctl.signal.aborted) return;
      toast(errorText(e), "err");
    }
  };

  const pickSlot = (id: string) => {
    setRightSel({ kind: "slot", slotId: id });
    const s = slots.find((x) => x.id === id);
    const m = s ? slotMapping(s) : null;
    if (m) setActiveMap(m.id);
    if (s && tpl) {
      const pos = slotPos(s, tpl.format) as any;
      if (pos?.kind === "cells") {
        if (tplSheet !== pos.sheet) setTplSheet(pos.sheet);
        setGridFocusR({ sheet: pos.sheet, r: pos.r1, c: pos.c1, key: Date.now() });
      } else if (pos?.kind === "rect") setTplPage(pos.page);
      setFocusR(`${m ? "tgt" : "slot"}:${m ? m.id : id}`);
      setTimeout(() => setFocusR(null), 50);
    }
  };

  const onMarkClickRight = (id: string) => {
    const m = mappings.find((x) => x.id === id);
    if (m) pickMapping(id);
    else pickSlot(id);
  };

  const selectedSlot = rightSel?.kind === "slot" ? slots.find((s) => s.id === rightSel.slotId) || null : null;
  // What a new comment can be about: the project, and whatever is selected right now.
  const commentOptions: AnchorOption[] = [
    { anchor: { kind: "project" }, label: tr("整个项目") },
    ...(selectedSlot ? [{ anchor: { kind: "slot" as const, id: selectedSlot.id }, label: tr("字段「{v1}」", { v1: selectedSlot.name }) }] : []),
    ...(activeMap ? [{ anchor: { kind: "mapping" as const, id: activeMap }, label: tr("当前映射") }] : []),
    ...(activeFile ? [{ anchor: { kind: "source" as const, id: activeFile }, label: tr("文件「{v1}」", { v1: sources.find(f => f.id === activeFile)?.filename || "" }) }] : []),
  ];
  const locateComment = (a: CommentAnchor) => {
    if (a.kind === "slot") { setSideCollapsed(false); setTab("fields"); setRightSel({ kind: "slot", slotId: a.id }); }
    else if (a.kind === "mapping") { setSideCollapsed(false); setTab("mappings"); setActiveMap(a.id); }
    else if (a.kind === "source") { setActiveFile(a.id); if (narrow) setMobilePane("source"); }
    else if (a.kind === "job") { nav(`/p/${pid}/jobs/${a.id}`); }
  };

  const startMapping = async () => {
    if (!file || !leftSel || !selectedSlot) return;
    try {
      const r = await post(`/api/sources/${file.id}/locator`, { selection: leftSel, project_id: pid, ...bq });
      const existing = slotMapping(selectedSlot) || null;
      setDialog({ kind: "mapping", slot: selectedSlot, proposals: r.proposals, warnings: r.warnings, existing, editing: null });
    } catch (e: any) {
      toast(errorText(e), isPermissionError(e) ? "info" : "err");
    }
  };

  const saveRelocation = async () => {
    if (!relocate || !leftSel) return;
    try {
      const r = await post(`/api/sources/${relocate.fileId}/locator`, { selection: leftSel, project_id: pid, ...bq });
      const m = mappings.find((x) => x.id === relocate.mappingId);
      if (!m || !r.proposals.length) throw new Error(tr("无法从当前选择生成定位"));
      const kind = m.sources[0]?.kind;
      const prop = r.proposals.find((x: Proposal) => x.locator.kind === kind) || r.proposals[0];
      // in 「每块一份」 an override belongs to the block being shown
      const key = blockIdx != null ? `${relocate.fileId}~${blockIdx}` : relocate.fileId;
      const where = blockIdx != null ? `「${file?.filename}」第 ${blockIdx + 1} 块` : `「${file?.filename}」`;
      await saveMappings(mappings.map((x) => (x.id === m.id ? { ...x, overrides: { ...(x.overrides || {}), [key]: { source_index: 0, locator: prop.locator } } } : x)), `映射 #${m.no} 在${where}中重新定位`);
      toast(tr("已为「{v1}」单独设置映射 #{v2} 的定位", { v1: file?.filename, v2: m.no }), "ok");
      setRelocate(null);
      setLeftSel(null);
    } catch (e: any) {
      toast(errorText(e), isPermissionError(e) ? "info" : "err");
    }
  };

  const createSlot = async (req: any) => {
    const t: TemplateDetail = await run(() => post(`/api/templates/${tpl!.id}/slots`, req), "t") as any;
    if (t.created_slot?.warning) toast(t.created_slot.warning);
    await reload();
    if (t.created_slot) setRightSel({ kind: "slot", slotId: t.created_slot.id });
    setTab("fields");
  };

  const updateSlot = async (id: string, patchv: any) => {
    await run(() => patch(`/api/templates/${tpl!.id}/slots/${id}`, patchv), "t");
    await reload();
  };

  const deleteSlot = async (s: Slot) => {
    const affected = mappings.filter((m) => (m.target.binding || m.target.slot_id) === (s.binding || s.id));
    const shared = slots.filter((x) => (x.binding || x.id) === (s.binding || s.id)).length > 1;
    const ok = await confirm(tr("删除字段"), <>{tr("删除字段「")}{s.name}{tr("」并恢复模板中原有内容？")}{affected.length > 0 && !shared && <><br /><b>{tr("同时会删除 ")}{formatNumber(affected.length)}{tr(" 个相关映射")}</b>（#{affected.map((m) => m.no).join("、#")}）。</>}{tr("可以撤销。")}</>, tr("删除"), true);
    if (!ok) return;
    try {
      await run(() => del(`/api/templates/${tpl!.id}/slots/${s.id}${qs({ project_id: pid, cascade: 1 })}`), "t");
    } catch {
      return;                                   // run() already said why
    }
    setRightSel(null);
    await reload();
  };

  const saveRule = async (r: RecordRule) => {
    await run(() => patch(`/api/projects/${pid}`, { record_rule: r, base_settings_rev: project?.settings_rev }));
    await reload();
    toast(tr("样本规则已保存"), "ok");
  };

  const runOcr = async () => {
    if (!file) return;
    setOcrBusy(true);
    try {
      const r = await post(`/api/sources/${file.id}/ocr`, {});
      toast(tr("OCR 完成：识别 {v1} 个词{v2}", { v1: r.words, v2: r.low_conf ? tr("，其中 {v1} 个低置信度（红色下划线）", { v1: r.low_conf }) : "" }), "ok");
      await reload();
    } catch (e: any) {
      toast(errorText(e), isPermissionError(e) ? "info" : "err");
    } finally {
      setOcrBusy(false);
    }
  };

  // ------------------------------------------------------------------ resizing
  const LEFT_MIN = 280;
  const leftMax = () => Math.max(LEFT_MIN, (workRef.current?.clientWidth || 1200) - 420);
  const leftPaneWidth = () => (workRef.current?.querySelector(".pane.left") as HTMLElement | null)?.getBoundingClientRect().width || 0;
  const setLeft = (w: number) => {
    const v = Math.round(Math.max(LEFT_MIN, Math.min(leftMax(), w)));
    setLeftW(v);
    remember("leftW", v);
    setTick((t) => t + 1);
  };
  // Pointer events with capture: mouse, pen and touch, and the drag cannot be lost when the
  // pointer crosses an iframe-like surface (the PDF layer) on the way.
  const dragResize = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const el = e.currentTarget;
    el.setPointerCapture?.(e.pointerId);
    const start = e.clientX;
    const w0 = leftPaneWidth();
    document.body.classList.add("resizing");
    const move = (ev: PointerEvent) => setLeftW(Math.max(LEFT_MIN, Math.min(leftMax(), w0 + ev.clientX - start)));
    const up = () => {
      document.body.classList.remove("resizing");
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointercancel", up);
      remember("leftW", leftPaneWidth());
      setTick((t) => t + 1);
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
  };
  // Keyboard alternative to dragging the splitter (WCAG 2.1.1 / 2.5.7, APG window splitter).
  const onResizerKey = (e: React.KeyboardEvent) => {
    if (isImeKey(e)) return;
    const stepPx = e.shiftKey ? 64 : 16;
    if (e.key === "ArrowLeft") { e.preventDefault(); setLeft(leftPaneWidth() - stepPx); }
    else if (e.key === "ArrowRight") { e.preventDefault(); setLeft(leftPaneWidth() + stepPx); }
    else if (e.key === "Home") { e.preventDefault(); setLeft(LEFT_MIN); }
    else if (e.key === "End") { e.preventDefault(); setLeft(leftMax()); }
    else if (e.key === "Enter") { e.preventDefault(); setLeftW(0); remember("leftW", 0); setTick((t) => t + 1); }
  };

  if (loadErr) return <PageState role="alert"><div className="notice err">{serverText(loadErr)}</div><Link to="/">{tr("返回项目列表")}</Link></PageState>;
  if (!project) return <PageState>{tr("加载项目…")}</PageState>;

  const readySources = sources.filter((s) => s.status === "ready" || s.status === "needs_ocr");
  const steps = [
    { n: 1, t: tr("上传来源"), done: readySources.length > 0, go: () => setFilesCollapsed(false) },
    { n: 2, t: tr("设置模板"), done: !!tpl, go: () => setDialog({ kind: "template" }) },
    { n: 3, t: tr("定义样本"), done: !!rule.mode, go: () => setDialog({ kind: "rule" }) },
    { n: 4, t: tr("建立映射"), done: mappings.length > 0, go: () => { setTab("mappings"); setSideCollapsed(false); } },
    { n: 5, t: tr("预览生成"), done: !!project.jobs?.length, go: () => setPreview(true) },
  ];
  const cur = steps.find((s) => !s.done)?.n ?? 5;
  const headerRow = rule.mode !== "per_file" && rule.table?.sheet && rule.table.header_row != null ? { sheet: rule.table.sheet, row: rule.table.header_row } : null;
  const canMap = !!(leftSel && selectedSlot && file && !relocate);
  const rawRight = rightSel && rightSel.kind !== "slot" ? rightSel : null;
  const lastJob = project.jobs?.[0];
  const activeMapping = activeMap ? mappings.find((m) => m.id === activeMap) || null : null;
  const editMapping = (m: Mapping) => {
    const s = mappingSlots(m, slots)[0];
    if (!s) { toast(tr("该映射的目标字段已不存在，请删除或重新建立"), "err"); return; }
    setDialog({ kind: "mapping", slot: s, proposals: [], warnings: [], existing: null, editing: m });
  };
  const toggleMapping = (m: Mapping) =>
    saveMappings(mappings.map((x) => (x.id === m.id ? { ...x, enabled: !x.enabled } : x)), `${m.enabled ? "禁用" : "启用"}映射 #${m.no}`).catch(() => {});
  const deleteMapping = async (m: Mapping) => {
    if (!(await confirm(tr("删除映射"), tr("删除映射 #{v1}？可以用「撤销」恢复。", { v1: m.no }), tr("删除"), true))) return;
    await saveMappings(mappings.filter((x) => x.id !== m.id), `删除映射 #${m.no}`).catch(() => {});
    setActiveMap(null);
  };

  // Every palette entry runs the same handler as its button – nothing here is a separate code path.
  // Only actions this member can actually take are listed: a viewer is not offered edits that
  // the server would refuse. Machine-interface management (keys, webhooks, ports, developer
  // documentation) never appears here — it is managed through the CLI and configuration only.
  const writeCommands: Command[] = ro ? [] : [
    { id: "upload", group: tr("工作流"), label: tr("上传来源文件…"), hint: tr("选择要上传的文件"), run: () => { setFilesCollapsed(false); setUploadReq((n) => n + 1); } },
    { id: "template", group: tr("工作流"), label: tr("设置 / 更换模板"), run: () => setDialog({ kind: "template" }) },
    { id: "versions", group: tr("工作流"), label: tr("模板版本历史…"), disabled: !tpl, run: () => setDialog({ kind: "versions" }) },
    { id: "rule", group: tr("工作流"), label: tr("定义样本规则"), hint: rule.mode ? ruleMode(rule.mode) : tr("未定义"), run: () => setDialog({ kind: "rule" }) },
    { id: "map", group: tr("工作流"), label: tr("建立映射"), keys: "⇄", hint: canMap ? "" : tr("先选中左侧来源和右侧字段"), disabled: !canMap, run: startMapping },
  ];
  const mappingCommands: Command[] = ro || !activeMapping ? [] : [
    { id: "map-edit", group: tr("当前映射 #{v1}", { v1: activeMapping.no }), label: tr("编辑映射 #{v1}", { v1: activeMapping.no }), run: () => editMapping(activeMapping) },
    { id: "map-toggle", group: tr("当前映射 #{v1}", { v1: activeMapping.no }), label: tr("{v1}映射 #{v2}", { v1: activeMapping.enabled ? tr("禁用") : tr("启用"), v2: activeMapping.no }), run: () => toggleMapping(activeMapping) },
    { id: "map-delete", group: tr("当前映射 #{v1}", { v1: activeMapping.no }), label: tr("删除映射 #{v1}…", { v1: activeMapping.no }), hint: tr("可以撤销"), run: () => deleteMapping(activeMapping) },
  ];
  const commands: Command[] = [
    ...mappingCommands,
    ...writeCommands,
    { id: "preview", group: tr("工作流"), label: preview ? tr("关闭预览") : tr("打开预览"), keys: singleKeys ? "P" : undefined, disabled: !tpl || !rule.mode, run: () => setPreview(!preview) },
    ...(ro ? [] : [
      { id: "generate", group: tr("工作流"), label: tr("批量生成…"), keys: "⌘↵", disabled: !tpl || !rule.mode, run: () => setDialog({ kind: "generate" }) },
      { id: "undo", group: tr("编辑"), label: tr("撤销"), keys: "⌘Z", disabled: !project.mappings.can_undo && !tpl?.can_undo, run: undo },
      { id: "redo", group: tr("编辑"), label: tr("重做"), keys: "⇧⌘Z", disabled: !project.mappings.can_redo && !tpl?.can_redo, run: redo },
    ] as Command[]),
    { id: "clear", group: tr("编辑"), label: tr("清除当前选择"), keys: "Esc", disabled: !leftSel && !rightSel, run: () => { setLeftSel(null); setRightSel(null); setActiveMap(null); } },
    { id: "find", group: tr("编辑"), label: tr("在来源文件中查找"), keys: "⌘F", disabled: !file || file.status === "parsing", run: () => { setPreview(false); setTimeout(() => findRef.current?.focus(), 0); } },
    ...(ro ? [] : [
      { id: "ocr", group: tr("编辑"), label: file?.ocr_pages?.length ? tr("重新 OCR 识别当前文件") : tr("OCR 识别当前文件"), disabled: !file || (file.family !== "image" && file.family !== "pdf"), run: runOcr },
    ] as Command[]),
    { id: "tab-map", group: tr("面板"), label: tr("查看映射列表"), run: () => { setSideCollapsed(false); setTab("mappings"); } },
    { id: "tab-field", group: tr("面板"), label: tr("查看模板字段"), run: () => { setSideCollapsed(false); setTab("fields"); } },
    { id: "tab-sample", group: tr("面板"), label: tr("查看样本列表"), run: () => { setSideCollapsed(false); setTab("samples"); } },
    { id: "tab-check", group: tr("面板"), label: tr("查看逐文件校验"), run: () => { setSideCollapsed(false); setTab("check"); } },
    { id: "side", group: tr("面板"), label: sideCollapsed ? tr("展开右侧面板") : tr("收起右侧面板（聚焦文档）"), run: () => setSideCollapsed(!sideCollapsed) },
    { id: "files", group: tr("面板"), label: filesCollapsed ? tr("展开文件列表") : tr("收起文件列表"), run: () => setFilesCollapsed(!filesCollapsed) },
    { id: "lines", group: tr("面板"), label: lines ? tr("隐藏映射连线") : tr("显示映射连线"), run: () => setLines(!lines) },
    { id: "width", group: tr("面板"), label: tr("恢复默认布局宽度"), disabled: !leftW && !sideW, run: () => { setLeftW(0); remember("leftW", 0); setSideW(0); setTick((t) => t + 1); } },
    { id: "zoom-reset", group: tr("面板"), label: tr("缩放恢复 100%"), disabled: zoomL === 1 && zoomR === 1, run: () => { setZoomL(1); setZoomR(1); } },
    { id: "help", group: tr("帮助"), label: tr("显示键盘快捷键"), keys: singleKeys ? "?" : undefined, run: () => setHelp(true) },
    ...(ro ? [] : [
      { id: "regression", group: tr("更多"), label: tr("保存为回归用例…"), disabled: !tpl || !rule.mode, run: () => setDialog({ kind: "regression" }) },
      { id: "desensitize", group: tr("更多"), label: tr("生成脱敏副本…"), disabled: !sources.length, run: () => setDialog({ kind: "desensitize" }) },
    ] as Command[]),
    { id: "job", group: tr("更多"), label: tr("打开最近的生成任务"), disabled: !lastJob, run: () => lastJob && nav(`/p/${pid}/jobs/${lastJob.id}`) },
    { id: "comments", group: tr("更多"), label: tr("评论…"), run: () => setComments(null) },
    { id: "regressions", group: tr("更多"), label: tr("回归用例列表"), run: () => nav("/regression") },
    { id: "caps", group: tr("更多"), label: tr("格式能力矩阵"), run: () => window.open("/capabilities", "_blank") },
    { id: "about", group: tr("更多"), label: tr("关于与许可"), run: () => window.open("/about", "_blank") },
    { id: "home", group: tr("更多"), label: tr("返回项目列表"), run: () => nav("/") },
    // every file, not only the first 20: typing filters the list
    ...sources.map((s) => ({
      id: `file-${s.id}`, group: tr("切换来源文件"), label: s.filename,
      hint: s.id === activeFile ? tr("当前") : s.representative ? tr("代表文件") : "", disabled: s.id === activeFile,
      run: () => setActiveFile(s.id),
    })),
  ];

  return (
    <div className="app">
      {/* ---------------------------------------------------------------- top bar */}
      <div className="topbar">
        <Link to="/" className="brand" title={tr("项目列表")}><BrandMark product="Docs" hideTextOnMobile /></Link>
        <LanguageMenu />
        <span className="sep" />
        <ProjectName key={project.name} name={project.name} readOnly={ro}
          onRename={(n) => run(() => patch(`/api/projects/${pid}`, { name: n, base_settings_rev: project.settings_rev })).then(reload)} />
        <span className="small muted desktop-only">{tr("模板：")}</span>
        <button type="button" className="btn ghost sm desktop-only" disabled={ro} onClick={() => setDialog({ kind: "template" })} title={ro ? tr(READ_ONLY_TEXT) : tr("更换或设置模板")}>{tpl ? `${tpl.name} · v${tpl.version.no}` : tr("未设置")} ▾</button>
        <span className="small muted desktop-only">{tr("样本：")}</span>
        <button type="button" className="btn ghost sm desktop-only" disabled={ro} title={ro ? tr(READ_ONLY_TEXT) : undefined} onClick={() => setDialog({ kind: "rule" })}>{ruleMode(rule.mode)} ▾</button>
        {ro ? <span className="badge" title={tr(READ_ONLY_TEXT)}>{tr("只读")}</span> : (
          <span className={`badge ${save === "saved" ? "ok" : save === "saving" ? "info" : "err"}`} title={tr("所有修改自动保存到服务器")} role="status">
            {save === "saved" ? tr("✓ 已自动保存") : save === "saving" ? tr("保存中…") : tr("保存失败")}
          </span>
        )}
        <span className="spacer" />
        <ProjectPresence room={room} slots={slots} mappings={mappings} sources={sources} onActivity={() => setActivityOpen(true)}
          onLocate={(f) => { if (f.kind === "slot") locateComment({ kind: "slot", id: f.id } as any); else if (f.kind === "mapping") locateComment({ kind: "mapping", id: f.id } as any); else setActiveFile(f.id); }} />
        {lastJob && <Link className="btn ghost sm" to={`/p/${pid}/jobs/${lastJob.id}`}>{tr("最近任务：")}{jobStatus(lastJob.status)[0]}</Link>}
        <button type="button" className="btn ghost sm" onClick={() => setCmdOpen(true)} title={tr("命令面板：所有操作与快捷键")} aria-label={tr("打开命令面板")}>
          ⌘K <span className="desktop-only tiny muted">{tr("命令")}</span>
        </button>
        <MoreMenu items={[
          ...(ro ? [] : [
            { label: tr("保存为回归用例…"), onClick: () => setDialog({ kind: "regression" }), disabled: !tpl || !rule.mode },
            { label: tr("生成脱敏副本…"), onClick: () => setDialog({ kind: "desensitize" }), disabled: !sources.length },
          ]),
          { label: tr("评论…"), onClick: () => setComments(null) },
          { label: tr("活动记录…"), onClick: () => setActivityOpen(true) },
          { label: tr("回归用例"), onClick: () => nav("/regression") },
          { label: tr("格式能力矩阵"), onClick: () => window.open("/capabilities", "_blank") },
          { label: tr("键盘快捷键"), onClick: () => setHelp(true) },
          { label: tr("关于与许可"), onClick: () => window.open("/about", "_blank") },
        ]} />
        <button type="button" className={`btn${preview ? " active" : ""}`} aria-pressed={preview} disabled={!tpl || !rule.mode} onClick={() => setPreview(!preview)}>{tr("👁 预览")}</button>
        <button type="button" className="btn primary" disabled={!tpl || !rule.mode || ro} title={ro ? tr(READ_ONLY_TEXT) : undefined} onClick={() => setDialog({ kind: "generate" })}>{tr("▶ 生成")}</button>
      </div>
      {project.copied_from && (
        <div className="notice info small" role="note" data-testid="copied-from">
          {tr("此项目由 {who} 的个人项目「{name}」经审批复制而来", { who: project.copied_from.requester?.name || "—", name: project.copied_from.source_project_name || "—" })}
          {project.copied_from.decider && <>{" · "}{project.copied_from.self_approved
            ? tr("{who} 自行批准", { who: project.copied_from.decider.name })
            : tr("由 {who} 批准", { who: project.copied_from.decider.name })}</>}
          {!project.copied_from.sources_included && <>{" · "}{tr("未包含来源文件，请上传本空间的数据后再生成")}</>}
          {project.copied_from.organization_id && <>{" · "}<a href={`${(session.state?.account_url || "/account").replace(/\/account$/, "")}/organizations/${project.copied_from.organization_id}?approval=${project.copied_from.request_id}`}>{tr("查看审批记录")}</a></>}
        </div>
      )}
      <div className="stepbar">
        {steps.map((s, i) => (
          <span key={s.n} className="row gap4">
            {i > 0 && <span className="step-arrow">→</span>}
            <button type="button" className={`step${s.done ? " done" : ""}${s.n === cur ? " cur" : ""}`} aria-current={s.n === cur ? "step" : undefined}
              disabled={ro && (s.n === 2 || s.n === 3)} onClick={s.go}><span className="n" aria-hidden="true">{s.done ? "✓" : s.n}</span>{s.t}{s.done ? tr("（已完成）") : ""}</button>
          </span>
        ))}
        <span className="spacer" />
        <label className="check small"><input type="checkbox" checked={lines} onChange={(e) => setLines(e.target.checked)} />{tr("显示连线")}</label>
        {!guide && <button className="btn ghost sm" onClick={() => setGuide(true)}>{tr("操作指引")}</button>}
      </div>
      {/* On a phone the two panes and the mapping list take turns. One is visible at a time; the
          selection bar underneath keeps both selections and the "建立映射" button in reach, so a
          mapping is made by choosing on 来源, switching to 目标 and choosing there — no side-by-side
          layout is needed to complete upload → map → preview → generate. */}
      <div className="pane-switch mobile-only" role="tablist" aria-label={tr("切换显示的面板")}>
        {([["files", tr("文件")], ["source", tr("来源")], ["target", preview ? tr("预览") : tr("目标")], ["map", tr("映射")]] as [MobilePane, string][]).map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={mobilePane === id} className={`btn sm${mobilePane === id ? " active" : ""}`}
            onClick={() => setMobilePane(id)}>
            {label}{id === "files" ? ` ${formatNumber(sources.length)}` : ""}{id === "source" && leftSel ? " ●" : ""}{id === "target" && (selectedSlot || rawRight) ? " ●" : ""}{id === "map" ? ` ${formatNumber(mappings.length)}` : ""}
          </button>
        ))}
      </div>
      {guide && (
        <div className="notice info row" style={{ borderRadius: 0, borderLeft: 0, borderRight: 0 }}>
          <span className="grow small">
            <b>{tr("使用步骤：")}</b>{tr("① 左侧上传来源文件 → ② 设置右侧目标模板（上传或新建）→ ③ 定义样本（每行 / 每文件 / 每组一份）→ ④ 左侧选中数据、右侧点选字段，点底部「建立映射」→ ⑤ 预览并生成。 任何一步都可以返回修改；Ctrl/⌘+Z 撤销，Esc 清除选择。")}</span>
          <button className="btn ghost sm" onClick={() => setGuide(false)}>{tr("不再显示")}</button>
        </div>
      )}
      {/* ---------------------------------------------------------------- work area */}
      <div className={`work m-${mobilePane}`} ref={workRef}>
        <FilePanel projectId={pid!} sources={sources} active={activeFile} onActive={(id) => setActiveFile(id)} onChanged={reload} collapsed={filesCollapsed && !narrow} onCollapse={setFilesCollapsed}
          readOnly={ro} canExport={perm.canExport} uploadRequest={uploadReq} />
        <div className="pane left" id="pane-left" style={{ flex: leftW ? `0 0 ${leftW}px` : "1 1 50%" }}>
          <div className="pane-h">
            <span className="title ellipsis">{file ? file.filename : tr("来源文件")}</span>
            {file && <span className="badge">{(file.format || "").toUpperCase()}</span>}
            {file?.representative && <span className="badge info">{tr("代表文件")}</span>}
            <span className="spacer" />
            {file && file.status !== "parsing" && (
              <span className="row gap4">
                <input ref={findRef} className="input sm" style={{ width: 120 }} placeholder={tr("查找…（⌘F）")} value={findQ} onChange={(e) => setFindQ(e.target.value)}
                  onKeyDown={(e) => {
                    // Enter that confirms a pinyin candidate must not search half-typed text,
                    // and Escape that cancels a candidate must not wipe the box.
                    if (isImeKey(e)) return;
                    if (e.key === "Enter") { e.preventDefault(); if (find && find.hits.length && e.shiftKey) gotoFind({ ...find, idx: (find.idx - 1 + find.hits.length) % find.hits.length }); else if (find && find.hits.length && findQ.trim() === find.q) gotoFind({ ...find, idx: (find.idx + 1) % find.hits.length }); else runFind().then(() => {}); }
                    if (e.key === "Escape") { setFind(null); setFindQ(""); }
                  }} aria-label={tr("在当前文件中查找")} />
                {find && <span className="tiny muted nowrap">{find.hits.length ? `${find.idx + 1}/${find.total}` : "0"}</span>}
                {find && find.hits.length > 1 && <>
                  <button type="button" className="btn sm icon" title={tr("上一个")} aria-label={tr("上一个查找结果")} onClick={() => gotoFind({ ...find, idx: (find.idx - 1 + find.hits.length) % find.hits.length })}>↑</button>
                  <button type="button" className="btn sm icon" title={tr("下一个")} aria-label={tr("下一个查找结果")} onClick={() => gotoFind({ ...find, idx: (find.idx + 1) % find.hits.length })}>↓</button>
                </>}
              </span>
            )}
            {file && (file.family === "image" || file.family === "pdf") && (
              <button type="button" className="btn sm" disabled={ocrBusy || ro} onClick={runOcr} title={ro ? tr(READ_ONLY_TEXT) : tr("使用本地文字识别（中文+英文）识别扫描页，不上传文件")}>
                {ocrBusy ? tr("识别中…") : file.ocr_pages.length ? tr("重新 OCR") : tr("OCR 识别")}
              </button>
            )}
            {file?.format === "csv" && !ro && <CsvOptions file={file} onDone={reload} />}
            <button type="button" className="btn sm icon" onClick={() => setZoomL(Math.max(0.5, +(zoomL - 0.1).toFixed(2)))} title={tr("缩小来源文件")} aria-label={tr("缩小来源文件")}>－</button>
            <span className="tiny muted" style={{ width: 34, textAlign: "center" }} aria-live="polite">{formatNumber(Math.round(zoomL * 100))}%</span>
            <button type="button" className="btn sm icon" onClick={() => setZoomL(Math.min(2.5, +(zoomL + 0.1).toFixed(2)))} title={tr("放大来源文件")} aria-label={tr("放大来源文件")}>＋</button>
            <button type="button" className="btn sm" onClick={() => setZoomL(1)} title={tr("适合宽度")}>{tr("适合")}</button>
          </div>
          {file?.notes?.length ? <div className="notice warn small" style={{ borderRadius: 0, maxHeight: 60, overflow: "auto" }}>{file.notes.map(serverText).join("; ")}</div> : null}
          {blockMode && file && (
            <div className="blocknav">
              <span className="small">{tr("表单块")}</span>
              {blocks.length ? (
                <>
                  <button type="button" className="btn xs icon" disabled={!blockIdx} onClick={() => { setLeftSel(null); setBlockIdx((blockIdx || 0) - 1); }} aria-label={tr("上一块")}>‹</button>
                  <select className="input sm" value={blockIdx ?? 0} onChange={(e) => { setLeftSel(null); setBlockIdx(Number(e.target.value)); }} aria-label={tr("选择表单块")}>
                    {blocks.map((b) => <option key={b.index} value={b.index}>{tr("第 {count} 块，共 {total} 块", { count: b.index + 1, total: blocks.length })} · {b.label.slice(0, 18)}</option>)}
                  </select>
                  <button type="button" className="btn xs icon" disabled={blockIdx == null || blockIdx >= blocks.length - 1} onClick={() => { setLeftSel(null); setBlockIdx((blockIdx || 0) + 1); }} aria-label={tr("下一块")}>›</button>
                  <span className="tiny muted ellipsis">{serverText(blocks[blockIdx ?? 0]?.where)}</span>
                </>
              ) : <span className="tiny muted">{tr("该文件中没有找到表单块标记")}</span>}
            </div>
          )}
          {relocate && (
            <div className="notice info small row" style={{ borderRadius: 0 }}>
              <span className="grow">{tr("重新定位映射 #")}{mappings.find((m) => m.id === relocate.mappingId)?.no}{tr("：请在「")}{file?.filename}{tr("」中选中正确的位置，然后点击保存。")}</span>
              <button className="btn sm primary" disabled={!leftSel} onClick={saveRelocation}>{tr("保存为此文件的定位")}</button>
              <button className="btn sm" onClick={() => setRelocate(null)}>{tr("取消")}</button>
            </div>
          )}
          <div className="pane-b">
            {!file ? (
              <div className="empty"><div className="big">{tr("第 1 步：上传来源文件")}</div><div>{ro ? tr("这个项目还没有来源文件。") : tr("把 XLSX / CSV / DOCX / PDF / 图片 / PPTX 等文件拖入左侧列表，或点击左侧的上传按钮")}</div></div>
            ) : (
              <AnyView selectable={!ro} view={srcView} side="src" reloadKey={srcKey}
                loadGrid={(s, off, lim) => get(`/api/sources/${file.id}/view${qs({ sheet: s, offset: off, limit: lim, ...bq })}`).then((r) => r.grid)}
                pageUrl={(p) => `/api/sources/${file.id}/page/${p}.png?zoom=1.5&v=${file.version}${bAmp}`}
                wordsUrl={(p) => `/api/sources/${file.id}/words/${p}?v=${file.version}${bAmp}`}
                marks={leftMarks} selection={leftSel} onSelect={setLeftSel} onMarkClick={pickMapping} zoom={zoomL}
                sheet={srcSheet} onSheet={setSrcSheet} page={srcPage} onPage={setSrcPage} headerRow={headerRow} gridFocus={gridFocusL} focusKey={focusL} />
            )}
          </div>
        </div>
        <div className="resizer" onPointerDown={dragResize} onKeyDown={onResizerKey} tabIndex={0} role="separator" aria-orientation="vertical"
          aria-label={tr("调整来源与模板两侧的宽度")} aria-controls="pane-left" aria-valuemin={LEFT_MIN} aria-valuemax={Math.round(leftMax())}
          aria-valuenow={Math.round(leftW || (workRef.current?.clientWidth || 0) / 2)} title={tr("拖动或用 ← / → 调整左右宽度，Enter 恢复默认")} />
        <div className="pane right" style={{ flex: "1 1 50%" }}>
          {preview ? (
            <PreviewPane projectId={pid!} rev={project.rev} mappings={mappings} slots={slots} onLocateMapping={(id) => { setPreview(false); setTimeout(() => pickMapping(id), 200); }} onClose={() => setPreview(false)} />
          ) : (
            <>
              <div className="pane-h">
                <span className="title ellipsis">{tpl ? tpl.name : tr("目标模板")}</span>
                {tpl && <span className="badge">{tpl.format.toUpperCase()} · v{tpl.version.no}</span>}
                <span className="spacer" />
                {tpl && (
                  <>
                    {!ro && <>
                      <button type="button" className="btn sm" disabled={!tpl.can_undo} onClick={() => step("undo", "t")} title={tr("撤销模板修改")} aria-label={tr("撤销模板修改")}>↶</button>
                      <button type="button" className="btn sm" disabled={!tpl.can_redo} onClick={() => step("redo", "t")} title={tr("重做模板修改")} aria-label={tr("重做模板修改")}>↷</button>
                      {(tpl.kind === "builtin_doc" || tpl.kind === "builtin_sheet") && <button type="button" className="btn sm" onClick={() => nav(`/templates/${tpl.id}/edit?project=${pid}`)}>{tr("编辑模板")}</button>}
                    </>}
                    <button type="button" className="btn sm" onClick={() => setDialog({ kind: "versions" })} title={tr("版本历史与恢复")}>{tr("版本")}</button>
                    {perm.canExport ? <>
                      <DownloadLink className="btn sm" href={`/api/templates/${tpl.id}/download?which=working`} title={tr("下载带字段标记的工作副本")}>{tr("工作副本")}</DownloadLink>
                      <DownloadLink className="btn sm" href={`/api/templates/${tpl.id}/download?which=original`} title={tr("下载未修改的原始模板")}>{tr("原始")}</DownloadLink>
                    </> : <span className="tiny muted" title={tr(NO_EXPORT_TEXT)}>{tr("无下载权限")}</span>}
                    <button type="button" className="btn sm icon" onClick={() => setZoomR(Math.max(0.5, +(zoomR - 0.1).toFixed(2)))} title={tr("缩小模板")} aria-label={tr("缩小模板")}>－</button>
                    <button type="button" className="btn sm icon" onClick={() => setZoomR(Math.min(2.5, +(zoomR + 0.1).toFixed(2)))} title={tr("放大模板")} aria-label={tr("放大模板")}>＋</button>
                  </>
                )}
              </div>
              {tpl?.info?.notes?.length ? <div className="notice warn small" style={{ borderRadius: 0, maxHeight: 60, overflow: "auto" }}>{tpl.info.notes.map(serverText).join("; ")}</div> : null}
              {tpl?.format === "pdf" && tpl.info?.pdf?.fields?.length > 0 && (
                <div className="toolbar small">{tr("PDF 表单字段：")}{tpl.info!.pdf.fields.map((f: any) => (
                  <button type="button" key={f.name} className="btn xs" disabled={ro} onClick={() => setDialog({ kind: "slot", sel: { kind: "pdf_field", field: f.name }, name: f.name })}>{f.name}</button>
                ))}</div>
              )}
              <div className="pane-b">
                {!tpl ? (
                  <div className="empty">
                    <div className="big">{tr("第 2 步：设置目标模板")}</div>
                    {project.template_missing && (
                      <div className="notice warn" role="alert">{tr("这个项目原来使用的模板已被删除。")}{ro ? tr("需要有编辑权限的成员重新选择模板。") : tr("请重新选择或新建一个模板。")}</div>
                    )}
                    <div>{ro ? tr("这个项目还没有设置模板。") : tr("上传已有的 DOCX / XLSX / PDF / PPTX，或在软件内新建文档型、表格型模板")}</div>
                    {!ro && <button type="button" className="btn primary" onClick={() => setDialog({ kind: "template" })}>{tr("设置模板")}</button>}
                  </div>
                ) : (
                  <AnyView selectable={!ro} view={tplView} side="tpl" reloadKey={tplKey}
                    loadGrid={(s, off, lim) => get(`/api/templates/${tpl.id}/view${qs({ sheet: s, offset: off, limit: lim })}`).then((r) => r.grid)}
                    pageUrl={(p) => `/api/templates/${tpl.id}/page/${p}.png?zoom=1.5&v=${tpl.head}`}
                    marks={rightMarks} selection={rightSel} onSelect={setRightSel} onMarkClick={onMarkClickRight} zoom={zoomR}
                    sheet={tplSheet} onSheet={setTplSheet} page={tplPage} onPage={setTplPage} gridFocus={gridFocusR} focusKey={focusR} slotNames={slotNames} />
                )}
              </div>
            </>
          )}
        </div>
        <SidePanel tab={tab} onTab={setTab} project={project} sources={sources} mappings={mappings} slots={slots} activeMap={activeMap}
          activeSlot={selectedSlot?.id || null} locate={locate} activeFile={activeFile} onPickMapping={pickMapping} onPickSlot={pickSlot}
          onEditMapping={editMapping}
          onSaveMappings={saveMappings} onUpdateSlot={updateSlot} onDeleteSlot={deleteSlot}
          onRelocate={(fid, mid) => { setActiveFile(fid); setRelocate({ fileId: fid, mappingId: mid }); setActiveMap(mid); }}
          onClearOverride={(key, mid) => { saveMappings(mappings.map((x) => { if (x.id !== mid) return x; const o = { ...(x.overrides || {}) }; delete o[key]; return { ...x, overrides: o }; }), "清除单独定位").catch(() => {}); }}
          onOpenRule={() => { if (!ro) setDialog({ kind: "rule" }); }}
          onCorrect={async (fid, mid, v) => { await run(() => put(`/api/sources/${fid}/corrections/${mid}`, { value: v, confirmed: true })).catch(() => {}); await reload(); }}
          collapsed={sideCollapsed && !narrow} onCollapse={setSideCollapsed}
          width={sideW} onWidth={(w) => { setSideW(w); setTick((t) => t + 1); }}
          canUndo={!!project.mappings.can_undo} canRedo={!!project.mappings.can_redo}
          onUndo={() => { step("undo", "m"); }} onRedo={() => { step("redo", "m"); }} readOnly={ro} />
        <Connectors container={workRef.current} mappings={mappings} active={activeMap} visible={lines && !preview} tick={tick + (project.rev || 0) + (leftW || 0)} />
      </div>
      {/* ---------------------------------------------------------------- selection bar */}
      <div className="selbar">
        <span className="small muted">{tr("来源：")}</span>
        <span className="small grow ellipsis" style={{ maxWidth: "32%" }}>{leftSel ? <b>{file?.filename} · {describeSelection(leftSel)}</b> : <span className="muted">{tr("在左侧选中数据（单元格、区域、文字或 PDF 框选）")}</span>}</span>
        {leftSel?.kind === "cells" && (leftSel.r2 > leftSel.r1) && rule.mode !== "per_file" && (
          <button type="button" className="btn sm" disabled={ro} onClick={() => setDialog({ kind: "area", sel: leftSel as any })} title={tr("明确这几行是「每行一份」还是「写入同一份文档的明细」")}>{tr("选中区域的含义…")}</button>
        )}
        <span className="small muted">{tr("→ 目标：")}</span>
        <span className="small grow ellipsis" style={{ maxWidth: "28%" }}>
          {selectedSlot ? <b>{tr("字段「")}{selectedSlot.name}」</b> : rawRight ? <b>{describeSelection(rawRight)}</b> : <span className="muted">{tr("在右侧点选字段，或选中内容后设为待填区域")}</span>}
        </span>
        {rawRight && tpl && !ro && (
          <button type="button" className="btn sm" onClick={() => setDialog({ kind: "slot", sel: rawRight, name: suggestName(rawRight, (rawRight as any).kind === "docx_text" ? findParaText(tplView, (rawRight as any).path, (rawRight as any).part) : undefined) })}>{tr("✎ 设为待填区域")}</button>
        )}
        <button type="button" className="btn primary" disabled={!canMap || ro} onClick={startMapping} title={ro ? tr(READ_ONLY_TEXT) : canMap ? "" : tr("需要同时选中左侧来源和右侧字段")}>{tr("⇄ 建立映射")}</button>
        {(leftSel || rightSel) && <button type="button" className="btn ghost sm" onClick={() => { setLeftSel(null); setRightSel(null); }}>{tr("清除选择")}</button>}
      </div>

      {/* ---------------------------------------------------------------- dialogs */}
      {activityOpen && <ActivityDrawer pid={pid!} slots={slots} seq={room.lastChange?.seq || 0} onClose={() => setActivityOpen(false)} />}
      {comments !== false && <CommentsDrawer pid={pid!} me={session.me?.id || null} canWrite={!ro} options={commentOptions} highlight={comments || null}
        onLocate={a => { locateComment(a); }} onClose={() => { setComments(false); if (new URLSearchParams(window.location.search).get("comment")) window.history.replaceState(window.history.state, "", window.location.pathname); }} />}
      {dialog?.kind === "template" && <TemplateChooser projectId={pid!} settingsRev={project.settings_rev} current={tpl?.id} onDone={reload} onClose={() => setDialog(null)} />}
      {dialog?.kind === "rule" && <RuleDialog project={project} sources={sources} initial={dialog.initial} selectedText={(leftSel as any)?.text} onSave={saveRule} onClose={() => setDialog(null)} />}
      {dialog?.kind === "slot" && tpl && <SlotDialog tpl={tpl} sel={dialog.sel} suggestedName={dialog.name} onCreate={createSlot} onClose={() => setDialog(null)} />}
      {dialog?.kind === "mapping" && tpl && (
        <MappingDialog project={project} slot={dialog.slot} slots={slots} fileId={activeFile} fileName={file?.filename || ""} proposals={dialog.proposals} warnings={dialog.warnings}
          existing={dialog.existing} editing={dialog.editing} templateFormat={tpl.format} sourceFormat={file?.format || ""}
          onSave={async (m, replaceId, note) => {
            let list: Mapping[];
            if (dialog.editing || (dialog.existing && m.id === dialog.existing.id)) list = mappings.map((x) => (x.id === m.id ? m : x));
            else if (replaceId) list = mappings.map((x) => (x.id === replaceId ? { ...m, id: replaceId, no: x.no, color: x.color } as Mapping : x));
            else list = [...mappings, m];
            await saveMappings(list, note || "修改映射");
            setLeftSel(null);
            toast(tr("映射已保存"), "ok");
          }}
          onClose={() => setDialog(null)} />
      )}
      {dialog?.kind === "versions" && tpl && <TemplateVersionsDialog tpl={tpl} canWrite={!ro} onChanged={reload} onClose={() => setDialog(null)} />}
      {dialog?.kind === "generate" && tpl && <GenerateDialog project={project} tpl={tpl} onStarted={(jid) => nav(`/p/${pid}/jobs/${jid}`)} onClose={() => setDialog(null)}
        onLocate={(target) => { setDialog(null); locateComment(target); }} />}
      {dialog?.kind === "regression" && <RegressionDialog project={project} onClose={() => setDialog(null)} />}
      {dialog?.kind === "desensitize" && <DesensitizeDialog projectId={pid!} sources={sources} onDone={reload} onClose={() => setDialog(null)} />}
      {dialog?.kind === "area" && <AreaDialog project={project} sel={dialog.sel} onPick={(r) => setDialog({ kind: "rule", initial: r })} onClose={() => setDialog(null)} />}
      {cmdOpen && <CommandPalette commands={commands} onClose={() => setCmdOpen(false)} />}
      {help && <Shortcuts onClose={() => setHelp(false)} singleKeys={singleKeys} onSingleKeys={setSingleKeys} />}
    </div>
  );
}

/** The project name in the top bar. It shows what the server holds: a rename that is refused
 *  (a viewer, a validation error) puts the stored name back instead of leaving the typed one on
 *  screen as if it had been saved. Enter commits, Escape reverts; neither acts mid-composition. */
function ProjectName({ name, readOnly, onRename }: { name: string; readOnly: boolean; onRename: (n: string) => Promise<unknown> }) {
  useI18n();
  const [v, setV] = useState(name);
  const commit = () => {
    const n = v.trim();
    if (!n || n === name) { setV(name); return; }
    onRename(n).catch(() => setV(name));
  };
  return (
    <input className="input" style={{ width: 200, fontWeight: 600, border: "1px solid transparent" }} value={v} readOnly={readOnly}
      title={readOnly ? tr(READ_ONLY_TEXT) : tr("点击修改项目名称")} aria-label={tr("项目名称")}
      onChange={(e) => setV(e.target.value)} onBlur={commit}
      onKeyDown={(e) => {
        if (isImeKey(e)) return;
        if (e.key === "Enter") { e.preventDefault(); (e.target as HTMLInputElement).blur(); }
        if (e.key === "Escape") { e.preventDefault(); setV(name); }
      }} />
  );
}

function findParaText(view: any, path: string, part: string): string | undefined {
  if (!view?.doc) return undefined;
  const walk = (blocks: any[]): string | undefined => {
    for (const b of blocks) {
      if (b.t === "p" && b.path === path && b.part === part) return b.text;
      if (b.t === "tbl") for (const r of b.rows) for (const c of r.cells) { const x = walk(c.blocks); if (x !== undefined) return x; }
    }
    return undefined;
  };
  for (const p of view.doc.parts) { const x = walk(p.blocks); if (x !== undefined) return x; }
  return undefined;
}

function CsvOptions({ file, onDone }: { file: SourceFile; onDone: () => void }) {
  useI18n();
  const [open, setOpen] = useState(false);
  const info = file.structure?.info || {};
  const [enc, setEnc] = useState(file.options?.csv?.encoding || "auto");
  const [delim, setDelim] = useState(file.options?.csv?.delimiter || "auto");
  const toast = useToast();
  return (
    <>
      <button type="button" className="btn sm" onClick={() => setOpen(true)} title={tr("编码与分隔符")}>{tr("CSV 设置（")}{info.encoding || "?"} / {info.delimiter === "\t" ? "Tab" : info.delimiter || "?"}）</button>
      {open && (
        // The shared Modal: focus moves in and stays in, Escape closes it, and page shortcuts
        // (P, ⌘Z) no longer act on the workspace behind it.
        <Modal title={tr("CSV 解析设置")} size="narrow" onClose={() => setOpen(false)}
          footer={<><span className="spacer" /><button type="button" className="btn" onClick={() => setOpen(false)}>{tr("取消")}</button>
            <button type="button" className="btn primary" onClick={async () => { try { await put(`/api/sources/${file.id}/options`, { csv: { encoding: enc, delimiter: delim } }); toast(tr("已重新解析"), "ok"); setOpen(false); onDone(); } catch (e: any) { toast(errorText(e), "err"); } }}>{tr("重新解析")}</button></>}>
            <div className="col">
              <div className="small muted">{tr("自动识别：编码 ")}{info.encoding}{tr("，分隔符「")}{info.delimiter === "\t" ? "Tab" : info.delimiter}{tr("」。如出现乱码或分列错误，请手动指定。单元格内换行（带引号）按 RFC 4180 处理，不会错误分行。")}</div>
              <label className="field">{tr("编码")}<select className="input" value={enc} onChange={(e) => setEnc(e.target.value)}>
                {["auto", "utf-8", "utf-8-sig", "gb18030", "gbk", "big5", "utf-16", "shift_jis", "latin-1"].map((x) => <option key={x} value={x}>{x === "auto" ? tr("自动识别") : x}</option>)}
              </select></label>
              <label className="field">{tr("分隔符")}<select className="input" value={delim} onChange={(e) => setDelim(e.target.value)}>
                <option value="auto">{tr("自动识别")}</option><option value=",">{tr("逗号 ,")}</option><option value="\t">Tab</option><option value=";">{tr("分号 ;")}</option><option value="|">{tr("竖线 |")}</option>
              </select></label>
            </div>
        </Modal>
      )}
    </>
  );
}

function AreaDialog({ project, sel, onPick, onClose }: { project: Project; sel: { sheet: string; r1: number; c1: number; r2: number; c2: number }; onPick: (r: Partial<RecordRule>) => void; onClose: () => void }) {
  useI18n();
  const [counts, setCounts] = useState<{ a?: number; b?: number }>({});
  const header = project.record_rule?.table?.header_row ?? Math.max(0, sel.r1 - 1);
  const rowsRule: RecordRule = { mode: "per_row", table: { sheet: sel.sheet, header_row: header, data_start: sel.r1 === header ? sel.r1 + 1 : sel.r1, data_end: sel.r2, skip_blank: true } };
  useEffect(() => {
    let alive = true;
    post(`/api/projects/${project.id}/rule-preview`, rowsRule).then((x) => alive && setCounts((c) => ({ ...c, a: x.total }))).catch(() => {});
    post(`/api/projects/${project.id}/rule-preview`, { mode: "per_file" }).then((x) => alive && setCounts((c) => ({ ...c, b: x.total }))).catch(() => {});
    return () => { alive = false; };
  }, []);
  const n = sel.r2 - sel.r1 + 1;
  // The two answers are buttons: they used to be <label>s with no control inside, which cannot
  // be reached or activated from the keyboard.
  return (
    <Modal title={tr("选中的 {v1} 行数据，代表什么？", { v1: n })} onClose={onClose}
      footer={<><span className="spacer" /><button type="button" className="btn" onClick={onClose}>{tr("取消")}</button></>}>
      <div className="col">
        <button type="button" className="opt" onClick={() => onPick(rowsRule)} data-autofocus>
          <div><div className="t">{tr("每行是一条样本 → 生成 ")}{counts.a ?? n}{tr(" 份文档")}</div><div className="small muted">{tr("第 {start}–{end} 行各生成一份（表头为第 {header} 行）；将打开样本规则确认。", { start: rowsRule.table!.data_start! + 1, end: sel.r2 + 1, header: header + 1 })}</div></div>
        </button>
        <button type="button" className="opt" onClick={() => onPick({ mode: "per_file" })}>
          <div><div className="t">{tr("整个区域是当前样本的明细表 → 每个文件生成 1 份（共 ")}{counts.b ?? "?"}{tr(" 份）")}</div><div className="small muted">{tr("这些行一起写入同一份文档的重复明细行；设置后请把该区域（含表头）映射到模板的重复明细字段。")}</div></div>
        </button>
      </div>
    </Modal>
  );
}
