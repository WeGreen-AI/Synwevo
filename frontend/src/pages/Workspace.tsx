import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, del, get, patch, post, put, qs } from "../lib/api";
import { describeSelection, mappingSlots, MODE_NAME, slotPos } from "../lib/describe";
import type { Mapping, Mark, Project, RecordRule, Selection, Slot, SourceFile, TemplateDetail } from "../lib/types";
import { useDialog, useToast } from "../lib/ui";
import { applyTheme, nextTheme, recall, remember, storedTheme, THEME_NAME, type Theme } from "../lib/theme";
import { jobStatus } from "../lib/status";
import AnyView from "../components/AnyView";
import CommandPalette, { Command } from "../components/CommandPalette";
import Shortcuts from "../components/Shortcuts";
import Connectors from "../components/Connectors";
import FilePanel from "../components/FilePanel";
import GenerateDialog from "../components/GenerateDialog";
import MappingDialog, { Proposal } from "../components/MappingDialog";
import PreviewPane from "../components/PreviewPane";
import RegressionDialog from "../components/RegressionDialog";
import DesensitizeDialog from "../components/DesensitizeDialog";
import MoreMenu from "../components/MoreMenu";
import RuleDialog from "../components/RuleDialog";
import SidePanel, { SideTab } from "../components/SidePanel";
import SlotDialog, { suggestName } from "../components/SlotDialog";
import TemplateChooser from "../components/TemplateChooser";

type Dialog =
  | null
  | { kind: "rule"; initial?: Partial<RecordRule> }
  | { kind: "template" }
  | { kind: "slot"; sel: Selection; name: string }
  | { kind: "mapping"; slot: Slot; proposals: Proposal[]; warnings: string[]; existing: Mapping | null; editing: Mapping | null }
  | { kind: "generate" }
  | { kind: "area"; sel: Selection & { kind: "cells" } }
  | { kind: "regression" }
  | { kind: "desensitize" };

const GREY = "#94a3b8";

export default function Workspace() {
  const { pid } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const { confirm } = useDialog();
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
  const [zoomL, setZoomL] = useState(() => recall("zoomL", 1));
  const [zoomR, setZoomR] = useState(() => recall("zoomR", 1));
  const [sideW, setSideW] = useState(() => recall("sideW", 0));
  const [cmdOpen, setCmdOpen] = useState(false);
  const [help, setHelp] = useState(false);
  const [theme, setTheme] = useState<Theme>(() => storedTheme());
  const [focusL, setFocusL] = useState<string | null>(null);
  const [focusR, setFocusR] = useState<string | null>(null);
  const [gridFocusL, setGridFocusL] = useState<any>(null);
  const [gridFocusR, setGridFocusR] = useState<any>(null);
  const [filesCollapsed, setFilesCollapsed] = useState(() => recall("filesCollapsed", false));
  const [sideCollapsed, setSideCollapsed] = useState(() => recall("sideCollapsed", false));
  const [relocate, setRelocate] = useState<{ fileId: string; mappingId: string } | null>(null);
  const [tick, setTick] = useState(0);
  const [guide, setGuide] = useState(() => recall("guide", true));
  const [ocrBusy, setOcrBusy] = useState(false);
  const [findQ, setFindQ] = useState("");
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

  const tpl: TemplateDetail | null = project?.template || null;
  const slots: Slot[] = tpl?.version.slots || [];
  const mappings: Mapping[] = project?.mappings.mappings || [];
  const rule = project?.record_rule || {};
  const file = sources.find((s) => s.id === activeFile) || null;

  // ------------------------------------------------------------------ loading
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
      if (loaded.current && transient) toast(e.message, "err");
      else setLoadErr(e.message);
      return null;
    }
  }, [pid, toast]);

  useEffect(() => {
    reload().then((p) => {
      if (p && !activeFile) {
        const rep = p.sources.find((s) => s.representative) || p.sources[0];
        if (rep) setActiveFile(rep.id);
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
    get(`/api/sources/${file.id}/blocks`).then((r) => {
      setBlocks(r.blocks || []);
      setBlockIdx((cur) => (r.blocks?.length ? (cur != null && cur < r.blocks.length ? cur : 0) : null));
      if (r.error) toast(r.error);
    }).catch(() => { setBlocks([]); setBlockIdx(null); });
  }, [file?.id, file?.version, file?.status, blockMode, blockRuleKey]);
  const bq = blockIdx != null ? { block: blockIdx } : {};
  const bAmp = blockIdx != null ? `&block=${blockIdx}` : "";

  // source view (non-grid parts are rendered from this response; grids page separately)
  const srcKey = file ? `${file.id}:${file.version}:${file.status}:${blockIdx ?? ""}` : "";
  useEffect(() => {
    setSrcView(null);
    setLeftSel(null); // never keep a selection from another file
    setFind(null);
    setSrcSheet(null);
    setSrcPage(0);
    if (!file) return;
    if (blockMode && blockIdx == null) return;   // wait for the block list
    get(`/api/sources/${file.id}/view${qs({ limit: 1, ...bq })}`).then(setSrcView).catch((e) => setSrcView({ view: "error", error: e.message }));
  }, [srcKey]);

  const tplKey = tpl ? `${tpl.id}:${tpl.head}` : "";
  useEffect(() => {
    if (!tpl) {
      setTplView(null);
      return;
    }
    get(`/api/templates/${tpl.id}/view${qs({ limit: 1 })}`).then(setTplView).catch((e) => setTplView({ view: "error", error: e.message }));
  }, [tplKey]);

  // where each mapping resolves in the active file
  useEffect(() => {
    if (!file || !mappings.length || file.status === "parsing") {
      setLocate({});
      return;
    }
    if (blockMode && blockIdx == null) return;
    get(`/api/projects/${pid}/locate${qs({ file: file.id, ...bq })}`).then(setLocate).catch(() => setLocate({}));
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
      // A 4xx from DocWeave means the server rejected this edit but holds a valid state of its
      // own, so the badge stays 已自动保存 and reload() resyncs. That reasoning does not extend
      // to a refusal the server never made: an intermediary's 403 and the auth gate's 401 both
      // mean the save did not arrive. The toast that says so disappears after 8s, and a stale
      // green ✓ is what the user is left looking at when they close the tab.
      const serverHasIt = e instanceof ApiError && !e.upstream && e.status > 401 && e.status < 500;
      setSave(serverHasIt ? "saved" : "error");
      toast(e.message, "err");
      throw e;
    }
  };

  const saveMappings = async (ms: Mapping[], note: string) => {
    await run(() => put(`/api/projects/${pid}/mappings`, { mappings: ms, note, base_rev: project?.mappings.rev }), "m");
    await reload();
  };

  const undo = async () => {
    const d = actions.current.undo.pop() || (project?.mappings.can_undo ? "m" : tpl?.can_undo ? "t" : undefined);
    if (!d) return;
    try {
      if (d === "m") await post(`/api/projects/${pid}/mappings/undo`);
      else await post(`/api/templates/${tpl!.id}/undo`);
      actions.current.redo.push(d);
      toast(d === "m" ? "已撤销映射修改" : "已撤销模板修改");
      await reload();
    } catch (e: any) {
      toast(e.message, "err");
    }
  };
  const redo = async () => {
    const d = actions.current.redo.pop() || (project?.mappings.can_redo ? "m" : tpl?.can_redo ? "t" : undefined);
    if (!d) return;
    try {
      if (d === "m") await post(`/api/projects/${pid}/mappings/redo`);
      else await post(`/api/templates/${tpl!.id}/redo`);
      actions.current.undo.push(d);
      await reload();
    } catch (e: any) {
      toast(e.message, "err");
    }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.isComposing || (e as any).keyCode === 229) return;   // never steal keys from an IME
      const t = e.target as HTMLElement;
      const typing = !!t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
      const mod = e.metaKey || e.ctrlKey;
      // the palette works from anywhere, including while typing in a field
      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdOpen((v) => !v);
        return;
      }
      if (mod && e.key.toLowerCase() === "f" && file && file.status !== "parsing" && !preview) {
        e.preventDefault();                                       // replace the browser find: ours searches the parsed file
        findRef.current?.focus();
        findRef.current?.select();
        return;
      }
      if (cmdOpen || help) return;
      if (typing) return;
      if (dialog) return;
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        e.shiftKey ? redo() : undo();
      } else if (mod && e.key.toLowerCase() === "y") {
        e.preventDefault();
        redo();
      } else if (mod && e.key === "Enter") {
        if (tpl && rule.mode) { e.preventDefault(); setDialog({ kind: "generate" }); }
      } else if (e.key === "?" || (e.key === "/" && e.shiftKey)) {
        e.preventDefault();
        setHelp(true);
      } else if (e.key === "p" && !mod && !e.altKey) {
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
      out.push({ id: "__find", color: "#f59e0b", role: "src", pos, active: true, label: `查找结果：${fh.text}` });
    }
    for (const m of mappings) {
      (locate[m.id] || []).forEach((r, i) => {
        if (!r.ok || !r.pos) return;
        out.push({ id: i ? `${m.id}~${i}` : m.id, no: m.no, color: m.color, role: "src", pos: r.pos, active: m.id === activeMap, dim: !m.enabled, label: `映射 #${m.no}` });
      });
    }
    return out;
  }, [mappings, locate, activeMap, find]);

  const rightMarks: Mark[] = useMemo(() => {
    if (!tpl) return [];
    const out: Mark[] = [];
    for (const s of slots) {
      const pos = slotPos(s, tpl.format);
      if (!pos) continue;
      const m = slotMapping(s);
      out.push({ id: m ? m.id : s.id, no: m?.no, color: m ? m.color : GREY, role: m ? "tgt" : "slot", pos, active: (m && m.id === activeMap) || (rightSel?.kind === "slot" && rightSel.slotId === s.id), label: `字段「${s.name}」` + (m ? `（映射 #${m.no}）` : "（未映射）") });
    }
    return out;
  }, [slots, mappings, activeMap, rightSel, tpl]);

  const slotNames = useMemo(() => {
    const o: Record<string, { name: string; color: string; no?: number; cols?: Record<string, string> }> = {};
    for (const s of slots) {
      const m = slotMapping(s);
      o[s.id] = { name: s.name, color: m ? m.color : GREY, no: m?.no, cols: Object.fromEntries((s.columns || []).map((c) => [c.key, c.name])) };
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
    if (!file || !findQ.trim()) { setFind(null); return; }
    try {
      const r = await get(`/api/sources/${file.id}/search${qs({ q: findQ.trim(), ...bq })}`);
      if (!r.hits.length) { toast(`「${findQ}」没有找到`); setFind({ hits: [], total: 0, idx: 0, q: findQ.trim() }); return; }
      gotoFind({ ...r, idx: 0, q: findQ.trim() });
    } catch (e: any) {
      toast(e.message, "err");
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

  const startMapping = async () => {
    if (!file || !leftSel || !selectedSlot) return;
    try {
      const r = await post(`/api/sources/${file.id}/locator`, { selection: leftSel, project_id: pid, ...bq });
      const existing = slotMapping(selectedSlot) || null;
      setDialog({ kind: "mapping", slot: selectedSlot, proposals: r.proposals, warnings: r.warnings, existing, editing: null });
    } catch (e: any) {
      toast(e.message, "err");
    }
  };

  const saveRelocation = async () => {
    if (!relocate || !leftSel) return;
    try {
      const r = await post(`/api/sources/${relocate.fileId}/locator`, { selection: leftSel, project_id: pid, ...bq });
      const m = mappings.find((x) => x.id === relocate.mappingId);
      if (!m || !r.proposals.length) throw new Error("无法从当前选择生成定位");
      const kind = m.sources[0]?.kind;
      const prop = r.proposals.find((x: Proposal) => x.locator.kind === kind) || r.proposals[0];
      // in 「每块一份」 an override belongs to the block being shown
      const key = blockIdx != null ? `${relocate.fileId}~${blockIdx}` : relocate.fileId;
      const where = blockIdx != null ? `「${file?.filename}」第 ${blockIdx + 1} 块` : `「${file?.filename}」`;
      await saveMappings(mappings.map((x) => (x.id === m.id ? { ...x, overrides: { ...(x.overrides || {}), [key]: { source_index: 0, locator: prop.locator } } } : x)), `映射 #${m.no} 在${where}中重新定位`);
      toast(`已为「${file?.filename}」单独设置映射 #${m.no} 的定位`, "ok");
      setRelocate(null);
      setLeftSel(null);
    } catch (e: any) {
      toast(e.message, "err");
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
    const ok = await confirm("删除字段", <>删除字段「{s.name}」并恢复模板中原有内容？{affected.length > 0 && !shared && <><br /><b>同时会删除 {affected.length} 个相关映射</b>（#{affected.map((m) => m.no).join("、#")}）。</>}可以撤销。</>, "删除", true);
    if (!ok) return;
    await run(() => del(`/api/templates/${tpl!.id}/slots/${s.id}${qs({ project_id: pid, cascade: 1 })}`), "t");
    setRightSel(null);
    await reload();
  };

  const saveRule = async (r: RecordRule) => {
    await run(() => patch(`/api/projects/${pid}`, { record_rule: r }));
    await reload();
    toast("样本规则已保存", "ok");
  };

  const runOcr = async () => {
    if (!file) return;
    setOcrBusy(true);
    try {
      const r = await post(`/api/sources/${file.id}/ocr`, {});
      toast(`OCR 完成：识别 ${r.words} 个词${r.low_conf ? `，其中 ${r.low_conf} 个低置信度（红色下划线）` : ""}`, "ok");
      await reload();
    } catch (e: any) {
      toast(e.message, "err");
    } finally {
      setOcrBusy(false);
    }
  };

  // ------------------------------------------------------------------ resizing
  const dragResize = (e: React.MouseEvent) => {
    const start = e.clientX;
    const container = workRef.current!;
    const leftPane = container.querySelector(".pane.left") as HTMLElement;
    const w0 = leftPane.getBoundingClientRect().width;
    document.body.classList.add("resizing");
    const move = (ev: MouseEvent) => {
      const w = Math.max(280, Math.min(container.clientWidth - 420, w0 + ev.clientX - start));
      setLeftW(w);
    };
    const up = () => {
      document.body.classList.remove("resizing");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      remember("leftW", leftPaneWidth());
      setTick((t) => t + 1);
    };
    const leftPaneWidth = () => (container.querySelector(".pane.left") as HTMLElement).getBoundingClientRect().width;
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  if (loadErr) return <div className="home"><div className="notice err">{loadErr}</div><Link to="/">返回项目列表</Link></div>;
  if (!project) return <div className="empty">加载项目…</div>;

  const readySources = sources.filter((s) => s.status === "ready" || s.status === "needs_ocr");
  const steps = [
    { n: 1, t: "上传来源", done: readySources.length > 0, go: () => setFilesCollapsed(false) },
    { n: 2, t: "设置模板", done: !!tpl, go: () => setDialog({ kind: "template" }) },
    { n: 3, t: "定义样本", done: !!rule.mode, go: () => setDialog({ kind: "rule" }) },
    { n: 4, t: "建立映射", done: mappings.length > 0, go: () => { setTab("mappings"); setSideCollapsed(false); } },
    { n: 5, t: "预览生成", done: !!project.jobs?.length, go: () => setPreview(true) },
  ];
  const cur = steps.find((s) => !s.done)?.n ?? 5;
  const headerRow = rule.mode !== "per_file" && rule.table?.sheet && rule.table.header_row != null ? { sheet: rule.table.sheet, row: rule.table.header_row } : null;
  const canMap = !!(leftSel && selectedSlot && file && !relocate);
  const rawRight = rightSel && rightSel.kind !== "slot" ? rightSel : null;
  const lastJob = project.jobs?.[0];
  const setThemeTo = (t: Theme) => { setTheme(t); applyTheme(t); };

  // Every palette entry runs the same handler as its button – nothing here is a separate code path.
  const commands: Command[] = [
    { id: "upload", group: "工作流", label: "上传来源文件", hint: "展开左侧文件列表", run: () => setFilesCollapsed(false) },
    { id: "template", group: "工作流", label: "设置 / 更换模板", run: () => setDialog({ kind: "template" }) },
    { id: "rule", group: "工作流", label: "定义样本规则", hint: rule.mode ? MODE_NAME[rule.mode] : "未定义", run: () => setDialog({ kind: "rule" }) },
    { id: "map", group: "工作流", label: "建立映射", keys: "⇄", hint: canMap ? "" : "先选中左侧来源和右侧字段", disabled: !canMap, run: startMapping },
    { id: "preview", group: "工作流", label: preview ? "关闭预览" : "打开预览", keys: "P", disabled: !tpl || !rule.mode, run: () => setPreview(!preview) },
    { id: "generate", group: "工作流", label: "批量生成…", keys: "⌘↵", disabled: !tpl || !rule.mode, run: () => setDialog({ kind: "generate" }) },
    { id: "undo", group: "编辑", label: "撤销", keys: "⌘Z", disabled: !project.mappings.can_undo && !tpl?.can_undo, run: undo },
    { id: "redo", group: "编辑", label: "重做", keys: "⇧⌘Z", disabled: !project.mappings.can_redo && !tpl?.can_redo, run: redo },
    { id: "clear", group: "编辑", label: "清除当前选择", keys: "Esc", disabled: !leftSel && !rightSel, run: () => { setLeftSel(null); setRightSel(null); setActiveMap(null); } },
    { id: "find", group: "编辑", label: "在来源文件中查找", keys: "⌘F", disabled: !file || file.status === "parsing", run: () => { setPreview(false); setTimeout(() => findRef.current?.focus(), 0); } },
    { id: "ocr", group: "编辑", label: file?.ocr_pages?.length ? "重新 OCR 识别当前文件" : "OCR 识别当前文件", disabled: !file || (file.family !== "image" && file.family !== "pdf"), run: runOcr },
    { id: "tab-map", group: "面板", label: "查看映射列表", run: () => { setSideCollapsed(false); setTab("mappings"); } },
    { id: "tab-field", group: "面板", label: "查看模板字段", run: () => { setSideCollapsed(false); setTab("fields"); } },
    { id: "tab-sample", group: "面板", label: "查看样本列表", run: () => { setSideCollapsed(false); setTab("samples"); } },
    { id: "tab-check", group: "面板", label: "查看逐文件校验", run: () => { setSideCollapsed(false); setTab("check"); } },
    { id: "side", group: "面板", label: sideCollapsed ? "展开右侧面板" : "收起右侧面板（聚焦文档）", run: () => setSideCollapsed(!sideCollapsed) },
    { id: "files", group: "面板", label: filesCollapsed ? "展开文件列表" : "收起文件列表", run: () => setFilesCollapsed(!filesCollapsed) },
    { id: "lines", group: "面板", label: lines ? "隐藏映射连线" : "显示映射连线", run: () => setLines(!lines) },
    { id: "width", group: "面板", label: "恢复默认布局宽度", disabled: !leftW && !sideW, run: () => { setLeftW(0); remember("leftW", 0); setSideW(0); setTick((t) => t + 1); } },
    { id: "zoom-reset", group: "面板", label: "缩放恢复 100%", disabled: zoomL === 1 && zoomR === 1, run: () => { setZoomL(1); setZoomR(1); } },
    { id: "theme", group: "外观", label: `界面主题：${THEME_NAME[theme]} → ${THEME_NAME[nextTheme(theme)]}`, hint: "不影响文档纸张与导出颜色", run: () => setThemeTo(nextTheme(theme)) },
    { id: "help", group: "外观", label: "显示键盘快捷键", keys: "?", run: () => setHelp(true) },
    { id: "regression", group: "更多", label: "保存为回归用例…", disabled: !tpl || !rule.mode, run: () => setDialog({ kind: "regression" }) },
    { id: "desensitize", group: "更多", label: "生成脱敏副本…", disabled: !sources.length, run: () => setDialog({ kind: "desensitize" }) },
    { id: "job", group: "更多", label: "打开最近的生成任务", disabled: !lastJob, run: () => lastJob && nav(`/p/${pid}/jobs/${lastJob.id}`) },
    { id: "regressions", group: "更多", label: "回归用例列表", run: () => nav("/regression") },
    { id: "caps", group: "更多", label: "格式能力矩阵", run: () => window.open("/capabilities", "_blank") },
    { id: "about", group: "更多", label: "关于与许可", run: () => window.open("/about", "_blank") },
    { id: "home", group: "更多", label: "返回项目列表", run: () => nav("/") },
    ...sources.slice(0, 20).map((s) => ({
      id: `file-${s.id}`, group: "切换来源文件", label: s.filename,
      hint: s.id === activeFile ? "当前" : s.representative ? "代表文件" : "", disabled: s.id === activeFile,
      run: () => setActiveFile(s.id),
    })),
  ];

  return (
    <div className="app">
      {/* ---------------------------------------------------------------- top bar */}
      <div className="topbar">
        <Link to="/" className="brand" title="项目列表"><span className="logo">W</span><span className="desktop-only">DocWeave</span></Link>
        <span className="sep" />
        <input className="input" style={{ width: 200, fontWeight: 600, border: "1px solid transparent" }} defaultValue={project.name}
          onBlur={(e) => e.target.value.trim() && e.target.value !== project.name && run(() => patch(`/api/projects/${pid}`, { name: e.target.value.trim() })).then(reload)} aria-label="项目名称" />
        <span className="small muted desktop-only">模板：</span>
        <button className="btn ghost sm desktop-only" onClick={() => setDialog({ kind: "template" })} title="更换或设置模板">{tpl ? `${tpl.name} · v${tpl.version.no}` : "未设置"} ▾</button>
        <span className="small muted desktop-only">样本：</span>
        <button className="btn ghost sm desktop-only" onClick={() => setDialog({ kind: "rule" })}>{rule.mode ? MODE_NAME[rule.mode] : "未定义"} ▾</button>
        <span className={`badge ${save === "saved" ? "ok" : save === "saving" ? "info" : "err"}`} title="所有修改自动保存到服务器">
          {save === "saved" ? "✓ 已自动保存" : save === "saving" ? "保存中…" : "保存失败"}
        </span>
        <span className="spacer" />
        {lastJob && <Link className="btn ghost sm" to={`/p/${pid}/jobs/${lastJob.id}`}>最近任务：{jobStatus(lastJob.status)[0]}</Link>}
        <button className="btn ghost sm" onClick={() => setCmdOpen(true)} title="命令面板：所有操作与快捷键">
          ⌘K <span className="desktop-only tiny muted">命令</span>
        </button>
        <MoreMenu items={[
          { label: "保存为回归用例…", onClick: () => setDialog({ kind: "regression" }), disabled: !tpl || !rule.mode },
          { label: "生成脱敏副本…", onClick: () => setDialog({ kind: "desensitize" }), disabled: !sources.length },
          { label: "回归用例", onClick: () => nav("/regression") },
          { label: "格式能力矩阵", onClick: () => window.open("/capabilities", "_blank") },
          { label: `界面主题：${THEME_NAME[theme]}`, onClick: () => setThemeTo(nextTheme(theme)) },
          { label: "键盘快捷键", onClick: () => setHelp(true) },
          { label: "关于与许可", onClick: () => window.open("/about", "_blank") },
        ]} />
        <button className={`btn${preview ? " active" : ""}`} disabled={!tpl || !rule.mode} onClick={() => setPreview(!preview)}>👁 预览</button>
        <button className="btn primary" disabled={!tpl || !rule.mode} onClick={() => setDialog({ kind: "generate" })}>▶ 生成</button>
      </div>
      <div className="stepbar">
        {steps.map((s, i) => (
          <span key={s.n} className="row gap4">
            {i > 0 && <span className="step-arrow">→</span>}
            <button className={`step${s.done ? " done" : ""}${s.n === cur ? " cur" : ""}`} onClick={s.go}><span className="n">{s.done ? "✓" : s.n}</span>{s.t}</button>
          </span>
        ))}
        <span className="spacer" />
        <label className="check small"><input type="checkbox" checked={lines} onChange={(e) => setLines(e.target.checked)} />显示连线</label>
        {!guide && <button className="btn ghost sm" onClick={() => setGuide(true)}>操作指引</button>}
      </div>
      <div className="notice warn mobile-only small" style={{ borderRadius: 0 }}>
        手机屏幕较小：建议在电脑浏览器中建立映射。手机上可以查看任务进度与下载结果（点击上方「最近任务」）。
      </div>
      {guide && (
        <div className="notice info row" style={{ borderRadius: 0, borderLeft: 0, borderRight: 0 }}>
          <span className="grow small">
            <b>使用步骤：</b>① 左侧上传来源文件 → ② 设置右侧目标模板（上传或新建）→ ③ 定义样本（每行 / 每文件 / 每组一份）→ ④ 左侧选中数据、右侧点选字段，点底部「建立映射」→ ⑤ 预览并生成。
            任何一步都可以返回修改；Ctrl/⌘+Z 撤销，Esc 清除选择。
          </span>
          <button className="btn ghost sm" onClick={() => setGuide(false)}>不再显示</button>
        </div>
      )}
      {/* ---------------------------------------------------------------- work area */}
      <div className="work" ref={workRef}>
        <FilePanel projectId={pid!} sources={sources} active={activeFile} onActive={(id) => setActiveFile(id)} onChanged={reload} collapsed={filesCollapsed} onCollapse={setFilesCollapsed} />
        <div className="pane left" style={{ flex: leftW ? `0 0 ${leftW}px` : "1 1 50%" }}>
          <div className="pane-h">
            <span className="title ellipsis">{file ? file.filename : "来源文件"}</span>
            {file && <span className="badge">{(file.format || "").toUpperCase()}</span>}
            {file?.representative && <span className="badge info">代表文件</span>}
            <span className="spacer" />
            {file && file.status !== "parsing" && (
              <span className="row gap4">
                <input ref={findRef} className="input sm" style={{ width: 120 }} placeholder="查找…（⌘F）" value={findQ} onChange={(e) => setFindQ(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") { if (find && find.hits.length && e.shiftKey) gotoFind({ ...find, idx: (find.idx - 1 + find.hits.length) % find.hits.length }); else if (find && find.hits.length && findQ.trim() === find.q) gotoFind({ ...find, idx: (find.idx + 1) % find.hits.length }); else runFind().then(() => {}); } if (e.key === "Escape") { setFind(null); setFindQ(""); } }} aria-label="在当前文件中查找" />
                {find && <span className="tiny muted nowrap">{find.hits.length ? `${find.idx + 1}/${find.total}` : "0"}</span>}
                {find && find.hits.length > 1 && <>
                  <button className="btn sm icon" title="上一个" onClick={() => gotoFind({ ...find, idx: (find.idx - 1 + find.hits.length) % find.hits.length })}>↑</button>
                  <button className="btn sm icon" title="下一个" onClick={() => gotoFind({ ...find, idx: (find.idx + 1) % find.hits.length })}>↓</button>
                </>}
              </span>
            )}
            {file && (file.family === "image" || file.family === "pdf") && (
              <button className="btn sm" disabled={ocrBusy} onClick={runOcr} title="使用本地 Tesseract（中文+英文）识别扫描页，不上传文件">
                {ocrBusy ? "识别中…" : file.ocr_pages.length ? "重新 OCR" : "OCR 识别"}
              </button>
            )}
            {file?.format === "csv" && <CsvOptions file={file} onDone={reload} />}
            <button className="btn sm icon" onClick={() => setZoomL(Math.max(0.5, +(zoomL - 0.1).toFixed(2)))} title="缩小">－</button>
            <span className="tiny muted" style={{ width: 34, textAlign: "center" }}>{Math.round(zoomL * 100)}%</span>
            <button className="btn sm icon" onClick={() => setZoomL(Math.min(2.5, +(zoomL + 0.1).toFixed(2)))} title="放大">＋</button>
            <button className="btn sm" onClick={() => setZoomL(1)} title="适合宽度">适合</button>
          </div>
          {file?.notes?.length ? <div className="notice warn small" style={{ borderRadius: 0, maxHeight: 60, overflow: "auto" }}>{file.notes.join("；")}</div> : null}
          {blockMode && file && (
            <div className="blocknav">
              <span className="small">表单块</span>
              {blocks.length ? (
                <>
                  <button className="btn xs icon" disabled={!blockIdx} onClick={() => { setLeftSel(null); setBlockIdx((blockIdx || 0) - 1); }} aria-label="上一块">‹</button>
                  <select className="input sm" value={blockIdx ?? 0} onChange={(e) => { setLeftSel(null); setBlockIdx(Number(e.target.value)); }} aria-label="选择表单块">
                    {blocks.map((b) => <option key={b.index} value={b.index}>第 {b.index + 1} / {blocks.length} 块 · {b.label.slice(0, 18)}</option>)}
                  </select>
                  <button className="btn xs icon" disabled={blockIdx == null || blockIdx >= blocks.length - 1} onClick={() => { setLeftSel(null); setBlockIdx((blockIdx || 0) + 1); }} aria-label="下一块">›</button>
                  <span className="tiny muted ellipsis">{blocks[blockIdx ?? 0]?.where}</span>
                </>
              ) : <span className="tiny muted">该文件中没有找到表单块标记</span>}
            </div>
          )}
          {relocate && (
            <div className="notice info small row" style={{ borderRadius: 0 }}>
              <span className="grow">重新定位映射 #{mappings.find((m) => m.id === relocate.mappingId)?.no}：请在「{file?.filename}」中选中正确的位置，然后点击保存。</span>
              <button className="btn sm primary" disabled={!leftSel} onClick={saveRelocation}>保存为此文件的定位</button>
              <button className="btn sm" onClick={() => setRelocate(null)}>取消</button>
            </div>
          )}
          <div className="pane-b">
            {!file ? (
              <div className="empty"><div className="big">第 1 步：上传来源文件</div><div>把 XLSX / CSV / DOCX / PDF / 图片 / PPTX 等文件拖入左侧列表</div></div>
            ) : (
              <AnyView view={srcView} side="src" reloadKey={srcKey}
                loadGrid={(s, off, lim) => get(`/api/sources/${file.id}/view${qs({ sheet: s, offset: off, limit: lim, ...bq })}`).then((r) => r.grid)}
                pageUrl={(p) => `/api/sources/${file.id}/page/${p}.png?zoom=1.5&v=${file.version}${bAmp}`}
                wordsUrl={(p) => `/api/sources/${file.id}/words/${p}?v=${file.version}${bAmp}`}
                marks={leftMarks} selection={leftSel} onSelect={setLeftSel} onMarkClick={pickMapping} zoom={zoomL}
                sheet={srcSheet} onSheet={setSrcSheet} page={srcPage} onPage={setSrcPage} headerRow={headerRow} gridFocus={gridFocusL} focusKey={focusL} />
            )}
          </div>
        </div>
        <div className="resizer" onMouseDown={dragResize} title="拖动调整左右宽度" />
        <div className="pane right" style={{ flex: "1 1 50%" }}>
          {preview ? (
            <PreviewPane projectId={pid!} rev={project.rev} mappings={mappings} slots={slots} onLocateMapping={(id) => { setPreview(false); setTimeout(() => pickMapping(id), 200); }} onClose={() => setPreview(false)} />
          ) : (
            <>
              <div className="pane-h">
                <span className="title ellipsis">{tpl ? tpl.name : "目标模板"}</span>
                {tpl && <span className="badge">{tpl.format.toUpperCase()} · v{tpl.version.no}</span>}
                <span className="spacer" />
                {tpl && (
                  <>
                    <button className="btn sm" disabled={!tpl.can_undo} onClick={async () => { await post(`/api/templates/${tpl.id}/undo`); actions.current.redo.push("t"); reload(); }} title="撤销模板修改">↶</button>
                    <button className="btn sm" disabled={!tpl.can_redo} onClick={async () => { await post(`/api/templates/${tpl.id}/redo`); reload(); }} title="重做模板修改">↷</button>
                    {(tpl.kind === "builtin_doc" || tpl.kind === "builtin_sheet") && <button className="btn sm" onClick={() => nav(`/templates/${tpl.id}/edit?project=${pid}`)}>编辑模板</button>}
                    <a className="btn sm" href={`/api/templates/${tpl.id}/download?which=working`} title="下载带字段标记的工作副本">工作副本</a>
                    <a className="btn sm" href={`/api/templates/${tpl.id}/download?which=original`} title="下载未修改的原始模板">原始</a>
                    <button className="btn sm icon" onClick={() => setZoomR(Math.max(0.5, +(zoomR - 0.1).toFixed(2)))}>－</button>
                    <button className="btn sm icon" onClick={() => setZoomR(Math.min(2.5, +(zoomR + 0.1).toFixed(2)))}>＋</button>
                  </>
                )}
              </div>
              {tpl?.info?.notes?.length ? <div className="notice warn small" style={{ borderRadius: 0, maxHeight: 60, overflow: "auto" }}>{tpl.info.notes.join("；")}</div> : null}
              {tpl?.format === "pdf" && tpl.info?.pdf?.fields?.length > 0 && (
                <div className="toolbar small">PDF 表单字段：{tpl.info!.pdf.fields.map((f: any) => (
                  <button key={f.name} className="btn xs" onClick={() => setDialog({ kind: "slot", sel: { kind: "pdf_field", field: f.name }, name: f.name })}>{f.name}</button>
                ))}</div>
              )}
              <div className="pane-b">
                {!tpl ? (
                  <div className="empty">
                    <div className="big">第 2 步：设置目标模板</div>
                    <div>上传已有的 DOCX / XLSX / PDF / PPTX，或在软件内新建文档型、表格型模板</div>
                    <button className="btn primary" onClick={() => setDialog({ kind: "template" })}>设置模板</button>
                  </div>
                ) : (
                  <AnyView view={tplView} side="tpl" reloadKey={tplKey}
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
          onEditMapping={(m) => { const s = mappingSlots(m, slots)[0]; if (!s) { toast("该映射的目标字段已不存在，请删除或重新建立", "err"); return; } setDialog({ kind: "mapping", slot: s, proposals: [], warnings: [], existing: null, editing: m }); }}
          onSaveMappings={saveMappings} onUpdateSlot={updateSlot} onDeleteSlot={deleteSlot}
          onRelocate={(fid, mid) => { setActiveFile(fid); setRelocate({ fileId: fid, mappingId: mid }); setActiveMap(mid); }}
          onClearOverride={async (fid, mid) => { await saveMappings(mappings.map((x) => { if (x.id !== mid) return x; const o = { ...(x.overrides || {}) }; delete o[fid]; return { ...x, overrides: o }; }), "清除单独定位"); }}
          onOpenRule={() => setDialog({ kind: "rule" })}
          onCorrect={async (fid, mid, v) => { await run(() => put(`/api/sources/${fid}/corrections/${mid}`, { value: v, confirmed: true })); await reload(); }}
          collapsed={sideCollapsed} onCollapse={setSideCollapsed}
          width={sideW} onWidth={(w) => { setSideW(w); setTick((t) => t + 1); }}
          canUndo={!!project.mappings.can_undo} canRedo={!!project.mappings.can_redo}
          onUndo={async () => { await post(`/api/projects/${pid}/mappings/undo`); reload(); }} onRedo={async () => { await post(`/api/projects/${pid}/mappings/redo`); reload(); }} />
        <Connectors container={workRef.current} mappings={mappings} active={activeMap} visible={lines && !preview} tick={tick + (project.rev || 0) + (leftW || 0)} />
      </div>
      {/* ---------------------------------------------------------------- selection bar */}
      <div className="selbar">
        <span className="small muted">来源：</span>
        <span className="small grow ellipsis" style={{ maxWidth: "32%" }}>{leftSel ? <b>{file?.filename} · {describeSelection(leftSel)}</b> : <span className="muted">在左侧选中数据（单元格、区域、文字或 PDF 框选）</span>}</span>
        {leftSel?.kind === "cells" && (leftSel.r2 > leftSel.r1) && rule.mode !== "per_file" && (
          <button className="btn sm" onClick={() => setDialog({ kind: "area", sel: leftSel as any })} title="明确这几行是「每行一份」还是「写入同一份文档的明细」">选中区域的含义…</button>
        )}
        <span className="small muted">→ 目标：</span>
        <span className="small grow ellipsis" style={{ maxWidth: "28%" }}>
          {selectedSlot ? <b>字段「{selectedSlot.name}」</b> : rawRight ? <b>{describeSelection(rawRight)}</b> : <span className="muted">在右侧点选字段，或选中内容后设为待填区域</span>}
        </span>
        {rawRight && tpl && (
          <button className="btn sm" onClick={() => setDialog({ kind: "slot", sel: rawRight, name: suggestName(rawRight, (rawRight as any).kind === "docx_text" ? findParaText(tplView, (rawRight as any).path, (rawRight as any).part) : undefined) })}>
            ✎ 设为待填区域
          </button>
        )}
        <button className="btn primary" disabled={!canMap} onClick={startMapping} title={canMap ? "" : "需要同时选中左侧来源和右侧字段"}>⇄ 建立映射</button>
        {(leftSel || rightSel) && <button className="btn ghost sm" onClick={() => { setLeftSel(null); setRightSel(null); }}>清除选择</button>}
      </div>

      {/* ---------------------------------------------------------------- dialogs */}
      {dialog?.kind === "template" && <TemplateChooser projectId={pid!} current={tpl?.id} onDone={reload} onClose={() => setDialog(null)} />}
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
            toast("映射已保存", "ok");
          }}
          onClose={() => setDialog(null)} />
      )}
      {dialog?.kind === "generate" && tpl && <GenerateDialog project={project} tpl={tpl} onStarted={(jid) => nav(`/p/${pid}/jobs/${jid}`)} onClose={() => setDialog(null)} />}
      {dialog?.kind === "regression" && <RegressionDialog project={project} onClose={() => setDialog(null)} />}
      {dialog?.kind === "desensitize" && <DesensitizeDialog projectId={pid!} sources={sources} onDone={reload} onClose={() => setDialog(null)} />}
      {dialog?.kind === "area" && <AreaDialog project={project} sel={dialog.sel} onPick={(r) => setDialog({ kind: "rule", initial: r })} onClose={() => setDialog(null)} />}
      <CommandPalette commands={commands} open={cmdOpen} onClose={() => setCmdOpen(false)} />
      {help && <Shortcuts onClose={() => setHelp(false)} />}
    </div>
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
  const [open, setOpen] = useState(false);
  const info = file.structure?.info || {};
  const [enc, setEnc] = useState(file.options?.csv?.encoding || "auto");
  const [delim, setDelim] = useState(file.options?.csv?.delimiter || "auto");
  const toast = useToast();
  return (
    <>
      <button className="btn sm" onClick={() => setOpen(true)} title="编码与分隔符">CSV 设置（{info.encoding || "?"} / {info.delimiter === "\t" ? "Tab" : info.delimiter || "?"}）</button>
      {open && (
        <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && setOpen(false)}>
          <div className="modal narrow">
            <div className="modal-h"><h2 className="grow">CSV 解析设置</h2></div>
            <div className="modal-b col">
              <div className="small muted">自动识别：编码 {info.encoding}，分隔符「{info.delimiter === "\t" ? "Tab" : info.delimiter}」。如出现乱码或分列错误，请手动指定。单元格内换行（带引号）按 RFC 4180 处理，不会错误分行。</div>
              <label className="field">编码<select className="input" value={enc} onChange={(e) => setEnc(e.target.value)}>
                {["auto", "utf-8", "utf-8-sig", "gb18030", "gbk", "big5", "utf-16", "shift_jis", "latin-1"].map((x) => <option key={x} value={x}>{x === "auto" ? "自动识别" : x}</option>)}
              </select></label>
              <label className="field">分隔符<select className="input" value={delim} onChange={(e) => setDelim(e.target.value)}>
                <option value="auto">自动识别</option><option value=",">逗号 ,</option><option value="\t">Tab</option><option value=";">分号 ;</option><option value="|">竖线 |</option>
              </select></label>
            </div>
            <div className="modal-f"><span className="spacer" /><button className="btn" onClick={() => setOpen(false)}>取消</button>
              <button className="btn primary" onClick={async () => { try { await put(`/api/sources/${file.id}/options`, { csv: { encoding: enc, delimiter: delim } }); toast("已重新解析", "ok"); setOpen(false); onDone(); } catch (e: any) { toast(e.message, "err"); } }}>重新解析</button></div>
          </div>
        </div>
      )}
    </>
  );
}

function AreaDialog({ project, sel, onPick, onClose }: { project: Project; sel: { sheet: string; r1: number; c1: number; r2: number; c2: number }; onPick: (r: Partial<RecordRule>) => void; onClose: () => void }) {
  const [counts, setCounts] = useState<{ a?: number; b?: number }>({});
  const header = project.record_rule?.table?.header_row ?? Math.max(0, sel.r1 - 1);
  const rowsRule: RecordRule = { mode: "per_row", table: { sheet: sel.sheet, header_row: header, data_start: sel.r1 === header ? sel.r1 + 1 : sel.r1, data_end: sel.r2, skip_blank: true } };
  useEffect(() => {
    post(`/api/projects/${project.id}/rule-preview`, rowsRule).then((x) => setCounts((c) => ({ ...c, a: x.total }))).catch(() => {});
    post(`/api/projects/${project.id}/rule-preview`, { mode: "per_file" }).then((x) => setCounts((c) => ({ ...c, b: x.total }))).catch(() => {});
  }, []);
  const n = sel.r2 - sel.r1 + 1;
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-h"><h2 className="grow">选中的 {n} 行数据，代表什么？</h2></div>
        <div className="modal-b col">
          <label className="opt" onClick={() => onPick(rowsRule)}>
            <div><div className="t">每行是一条样本 → 生成 {counts.a ?? n} 份文档</div><div className="small muted">第 {rowsRule.table!.data_start! + 1}–{sel.r2 + 1} 行各生成一份（表头为第 {header + 1} 行）；将打开样本规则确认。</div></div>
          </label>
          <label className="opt" onClick={() => onPick({ mode: "per_file" })}>
            <div><div className="t">整个区域是当前样本的明细表 → 每个文件生成 1 份（共 {counts.b ?? "?"} 份）</div><div className="small muted">这些行一起写入同一份文档的重复明细行；设置后请把该区域（含表头）映射到模板的重复明细字段。</div></div>
          </label>
        </div>
        <div className="modal-f"><span className="spacer" /><button className="btn" onClick={onClose}>取消</button></div>
      </div>
    </div>
  );
}
