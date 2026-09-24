import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { EditorContent, Node, mergeAttributes, useEditor, Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Table, TableCell, TableHeader, TableRow } from "@tiptap/extension-table";
import { TextStyle, Color, FontSize, FontFamily } from "@tiptap/extension-text-style";
import TextAlign from "@tiptap/extension-text-align";
import Image from "@tiptap/extension-image";
import Highlight from "@tiptap/extension-highlight";
import { get, post, put } from "@synwevo/auth-client";
import { useDialog, useToast } from "@synwevo/ui";
import { cssColor } from "../lib/css";
import { errorText } from "../lib/perm";
import { loadedVersion, saveOverStale, STALE_KEPT, STALE_TITLE, staleText } from "../lib/templatesave";

const rid = () => "f" + Math.random().toString(16).slice(2, 12);

const Field = Node.create({
  name: "field",
  group: "inline",
  inline: true,
  atom: true,
  selectable: true,
  addAttributes() {
    return { id: { default: null }, name: { default: "字段" }, required: { default: false } };
  },
  parseHTML() {
    return [{ tag: "span[data-field]" }];
  },
  renderHTML({ node, HTMLAttributes }) {
    return ["span", mergeAttributes(HTMLAttributes, { "data-field": node.attrs.id, class: `field-node${node.attrs.required ? " req" : ""}`, title: `字段：${node.attrs.name}` }), `【${node.attrs.name}】`];
  },
});

const PageBreak = Node.create({
  name: "pageBreak",
  group: "block",
  atom: true,
  parseHTML() {
    return [{ tag: "div[data-page-break]" }];
  },
  renderHTML() {
    return ["div", { "data-page-break": "1", class: "page-break" }];
  },
});

/** A group of blocks copied once per detail row (fields inside become the detail columns). */
const RepeatBlock = Node.create({
  name: "repeatBlock",
  group: "block",
  content: "(paragraph | heading | bulletList | orderedList | image | horizontalRule)+",
  defining: true,
  addAttributes() {
    return {
      repeatId: { default: null, renderHTML: () => ({}) },
      repeatName: { default: "明细", renderHTML: () => ({}) },
      separator: { default: "none", renderHTML: () => ({}) },
      emptyPolicy: { default: "remove", renderHTML: () => ({}) },
    };
  },
  parseHTML() {
    return [{ tag: "div[data-repeat-block]" }];
  },
  renderHTML({ node, HTMLAttributes }) {
    return ["div", mergeAttributes(HTMLAttributes, { "data-repeat-block": node.attrs.repeatId || "", class: "repeat-block", "data-label": `⟳ 重复内容块：${node.attrs.repeatName || "明细"}` }), 0];
  },
});

const RepeatRow = TableRow.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      repeat: { default: false, renderHTML: (a: any) => (a.repeat ? { "data-repeat": "true" } : {}) },
      repeatId: { default: null, renderHTML: () => ({}) },
      repeatName: { default: null, renderHTML: () => ({}) },
      emptyPolicy: { default: "remove_row", renderHTML: () => ({}) },
    };
  },
});

/** Cell shading. The stored editor JSON reaches this attribute straight from the API, so only a
 *  plain colour is written into the style attribute — never a url() someone saved there. */
const CellBg = (Base: any) => Base.extend({
  addAttributes() {
    return { ...this.parent?.(), background: { default: null, renderHTML: (a: any) => { const c = cssColor(a.background); return c ? { style: `background:${c}` } : {}; } } };
  },
});

const extensions = (placeholder?: string) => [
  StarterKit.configure({ link: false, code: false, codeBlock: false } as any),
  Table.configure({ resizable: false }),
  RepeatRow,
  CellBg(TableHeader),
  CellBg(TableCell),
  TextStyle,
  Color,
  FontSize,
  FontFamily,
  TextAlign.configure({ types: ["heading", "paragraph"] }),
  Image.configure({ inline: false, allowBase64: true }),
  Highlight,
  Field,
  PageBreak,
  RepeatBlock,
];

const PAPERS: Record<string, [number, number]> = { A4: [210, 297], A3: [297, 420], A5: [148, 210], B5: [176, 250], Letter: [215.9, 279.4], Legal: [215.9, 355.6] };
const EMPTY = { type: "doc", content: [{ type: "paragraph" }] };

export default function DocEditor() {
  const { tid } = useParams();
  const [sp] = useSearchParams();
  const projectId = sp.get("project");
  const nav = useNavigate();
  const toast = useToast();
  const { prompt, confirm } = useDialog();
  // The version this editor loaded (or last saved): sent as base_version so a save never
  // replaces a newer version without asking.
  const baseVersion = useRef<number | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [name, setName] = useState("文档模板");
  const [page, setPage] = useState<any>({ size: "A4", orientation: "portrait", margins: { top: 25.4, bottom: 25.4, left: 31.8, right: 31.8 } });
  const [loaded, setLoaded] = useState(!tid);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const focused = useRef<Editor | null>(null);
  const imgInput = useRef<HTMLInputElement>(null);
  const [, force] = useState(0);

  const mk = (content: any) => ({
    extensions: extensions(),
    content,
    onFocus: ({ editor }: any) => { focused.current = editor; force((x) => x + 1); },
    onUpdate: () => setDirty(true),
    onSelectionUpdate: () => force((x) => x + 1),
    // The row / block attribute inputs read the editor state while rendering, and @tiptap/react
    // does not re-render on transactions by default: once the document was dirty, each keystroke
    // in 明细名称 was reset to the old value and the policy selects showed a stale choice.
    onTransaction: () => force((x) => x + 1),
  });
  const body = useEditor(mk(EMPTY));
  const header = useEditor(mk(EMPTY));
  const footer = useEditor(mk(EMPTY));

  useEffect(() => {
    if (!tid || !body || !header || !footer) return;
    let alive = true;
    get(`/api/templates/${tid}`).then((t) => {
      if (!alive) return;
      if (t.kind !== "builtin_doc") { setLoadErr("这个模板不是用文档编辑器创建的，不能在这里编辑。"); return; }
      setName(t.name);
      baseVersion.current = loadedVersion(t);
      const ed = t.version.editor || {};
      if (ed.page) setPage(ed.page);
      body.commands.setContent(ed.body || EMPTY);
      header.commands.setContent(ed.header || EMPTY);
      footer.commands.setContent(ed.footer || EMPTY);
      setLoaded(true);
      setDirty(false);
    }).catch((e) => { if (alive) setLoadErr(errorText(e)); });
    return () => { alive = false; };
  }, [tid, body, header, footer]);

  // In-app navigation does not fire beforeunload, so 返回 asks itself before discarding edits.
  const back = async () => {
    if (dirty && !(await confirm("放弃未保存的修改？", "模板有尚未保存的修改，离开后这些修改会丢失。", "放弃修改并离开", true))) return;
    nav(projectId ? `/p/${projectId}` : "/");
  };

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); } };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const ed = () => focused.current || body!;
  const chain = () => ed().chain().focus();

  const insertField = async () => {
    const target = ed();
    const n = await prompt("插入字段", "字段名称（生成时替换为数据）", "");
    if (!n) return;
    // The prompt restores its opener during cleanup. Resume typing in the document after that
    // cleanup, otherwise Enter reopens the insert-field prompt instead of starting a paragraph.
    requestAnimationFrame(() => {
      target.view.focus();
      target.commands.insertContent({ type: "field", attrs: { id: rid(), name: n.trim(), required: false } });
    });
  };

  const toggleRepeat = () => {
    const e = body!;
    if (!e.isActive("table")) {
      toast("请先把光标放在表格的某一行中");
      return;
    }
    const cur = e.getAttributes("tableRow");
    if (cur.repeat) {
      e.chain().focus().updateAttributes("tableRow", { repeat: false, repeatId: null }).run();
    } else {
      e.chain().focus().updateAttributes("tableRow", { repeat: true, repeatId: cur.repeatId || rid(), repeatName: cur.repeatName || "明细" }).run();
      toast("已设为重复明细行：行内插入的字段即明细列；生成时每条明细复制一行");
    }
  };

  const inBlock = body?.isActive("repeatBlock");
  const blockAttrs = inBlock ? body!.getAttributes("repeatBlock") : {};
  const toggleBlock = () => {
    const e = body!;
    if (e.isActive("table")) {
      toast("表格中请使用「设为重复明细行」；重复内容块用于表格以外的段落");
      return;
    }
    if (e.isActive("repeatBlock")) {
      e.chain().focus().lift("repeatBlock").run();
      toast("已取消重复内容块（段落保留）");
      return;
    }
    if (!e.chain().focus().wrapIn("repeatBlock", { repeatId: rid(), repeatName: "明细" }).run()) {
      toast("无法把选中的内容设为重复内容块（请选中表格以外的连续段落）", "err");
      return;
    }
    toast("已设为重复内容块：块内插入的字段就是明细列；生成时每条明细复制一份这些段落");
  };
  const setBlock = (attrs: any) => body!.chain().updateAttributes("repeatBlock", attrs).run();

  /**
   * Insert a picked image, re-encoded to something a page can actually use.
   *
   * It used to insert the file's own data URI at full resolution and merely *display* it at
   * 600px. A phone photo is 4000px wide and ~11 MB, which becomes ~16 MB of base64 living
   * inside the document model — and the model is what `saveTpl` submits, so every save carried
   * the whole camera original. Several of those is an out-of-memory kill on a small container
   * rather than an error page.
   *
   * 1600px on the long edge is roughly 135mm at 300dpi: generous for print, and a ~50x
   * reduction from a phone original. PNG is kept as PNG so transparency survives; everything
   * else becomes JPEG, which is what actually removes the bulk.
   */
  const onImage = (f: File) => {
    const MAX_EDGE = 1600;
    const r = new FileReader();
    r.onload = () => {
      const img = new window.Image();
      img.onload = () => {
        const scale = Math.min(1, MAX_EDGE / Math.max(img.width, img.height));
        let src = String(r.result);
        if (scale < 1) {
          const canvas = document.createElement("canvas");
          canvas.width = Math.round(img.width * scale);
          canvas.height = Math.round(img.height * scale);
          const ctx = canvas.getContext("2d");
          if (ctx) {
            ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
            const keepAlpha = f.type === "image/png";
            // A failed re-encode leaves `src` as the original: worse for size, still correct.
            src = canvas.toDataURL(keepAlpha ? "image/png" : "image/jpeg",
                                   keepAlpha ? undefined : 0.85) || src;
          }
        }
        chain().setImage({ src, width: Math.min(img.width, 600) } as any).run();
      };
      img.src = String(r.result);
    };
    r.readAsDataURL(f);
  };

  const saveTpl = async () => {
    if (!body) return;
    setBusy(true);
    const editor = { page, body: body.getJSON(), header: header!.getJSON(), footer: footer!.getJSON() };
    try {
      let t;
      if (tid) {
        t = await saveOverStale((base) => put(`/api/templates/${tid}/builtin`, { editor, name, ...(base ? { base_version: base } : {}) }),
          baseVersion.current, (theirs, mine) => confirm(STALE_TITLE, staleText(theirs, mine), "仍然保存", true));
        if (!t) { toast(STALE_KEPT, "info"); return; }
      } else t = await post("/api/templates/builtin", { kind: "builtin_doc", name, editor, project_id: projectId });
      baseVersion.current = loadedVersion(t);
      setDirty(false);
      toast(`已保存（${t.version.slots.length} 个字段，v${t.version.no}）`, "ok");
      if (!tid) nav(`/templates/${t.id}/edit${projectId ? `?project=${projectId}` : ""}`, { replace: true });
    } catch (e: any) {
      toast(errorText(e), "err");
    } finally {
      setBusy(false);
    }
  };

  const [pw, ph] = PAPERS[page.size] || PAPERS.A4;
  const W = (page.orientation === "landscape" ? ph : pw) * 3.78;
  const m = page.margins;
  const e = focused.current || body;
  const inTable = body?.isActive("table");
  const rowAttrs = inTable ? body!.getAttributes("tableRow") : {};

  if (loadErr) return (
    <div className="empty" role="alert">
      <div className="big">无法打开模板</div>
      <div className="notice err" style={{ maxWidth: 480 }}>{loadErr}</div>
      <button type="button" className="btn" onClick={() => nav(projectId ? `/p/${projectId}` : "/")}>返回</button>
    </div>
  );

  return (
    <div className="editor-shell" inert={busy || !loaded}>
      <div className="topbar">
        <button type="button" className="btn ghost sm" onClick={back}>← 返回</button>
        <input className="input" value={name} onChange={(ev) => { setName(ev.target.value); setDirty(true); }} style={{ width: 240, fontWeight: 600 }} aria-label="模板名称" />
        <span className="badge">文档型模板 → DOCX</span>
        {!loaded ? <span className="badge info" role="status">加载中…</span> : dirty ? <span className="badge warn" role="status">未保存</span> : <span className="badge ok" role="status">已保存</span>}
        <span className="spacer" />
        <button type="button" className="btn primary" disabled={busy || !loaded} onClick={saveTpl}>{busy ? "保存中…" : "保存模板"}</button>
      </div>
      <div className="toolbar">
        <select className="input sm" aria-label="段落样式" value={e?.isActive("heading", { level: 1 }) ? "h1" : e?.isActive("heading", { level: 2 }) ? "h2" : e?.isActive("heading", { level: 3 }) ? "h3" : "p"}
          onChange={(ev) => { const v = ev.target.value; v === "p" ? chain().setParagraph().run() : chain().setHeading({ level: Number(v[1]) as any }).run(); }}>
          <option value="p">正文</option><option value="h1">标题 1</option><option value="h2">标题 2</option><option value="h3">标题 3</option>
        </select>
        <select className="input sm" aria-label="字号" onChange={(ev) => { ev.target.value ? chain().setFontSize(ev.target.value).run() : chain().unsetFontSize().run(); }} defaultValue="">
          <option value="">字号</option>{["9pt", "10pt", "10.5pt", "12pt", "14pt", "16pt", "18pt", "22pt", "26pt"].map((x) => <option key={x}>{x}</option>)}
        </select>
        <select className="input sm" aria-label="字体" onChange={(ev) => { ev.target.value ? chain().setFontFamily(ev.target.value).run() : chain().unsetFontFamily().run(); }} defaultValue="">
          <option value="">字体</option>{["宋体", "黑体", "楷体", "仿宋", "微软雅黑", "Arial", "Times New Roman"].map((x) => <option key={x}>{x}</option>)}
        </select>
        <button type="button" className={`btn sm${e?.isActive("bold") ? " active" : ""}`} aria-pressed={!!e?.isActive("bold")} aria-label="加粗" title="加粗" onClick={() => chain().toggleBold().run()}><b>B</b></button>
        <button type="button" className={`btn sm${e?.isActive("italic") ? " active" : ""}`} aria-pressed={!!e?.isActive("italic")} aria-label="斜体" title="斜体" onClick={() => chain().toggleItalic().run()}><i>I</i></button>
        <button type="button" className={`btn sm${e?.isActive("underline") ? " active" : ""}`} aria-pressed={!!e?.isActive("underline")} aria-label="下划线" title="下划线" onClick={() => chain().toggleUnderline().run()}><u>U</u></button>
        <button type="button" className={`btn sm${e?.isActive("strike") ? " active" : ""}`} aria-pressed={!!e?.isActive("strike")} aria-label="删除线" title="删除线" onClick={() => chain().toggleStrike().run()}><s>S</s></button>
        <input type="color" title="文字颜色" aria-label="文字颜色" onChange={(ev) => chain().setColor(ev.target.value).run()} style={{ width: 28, height: 26, border: 0, background: "none" }} />
        <button type="button" className={`btn sm${e?.isActive("highlight") ? " active" : ""}`} aria-pressed={!!e?.isActive("highlight")} aria-label="高亮" onClick={() => chain().toggleHighlight().run()} title="高亮">▇</button>
        <span className="sep" style={{ width: 1, height: 20, background: "var(--line)" }} />
        {(["left", "center", "right", "justify"] as const).map((a) => <button key={a} className={`btn sm${e?.isActive({ textAlign: a }) ? " active" : ""}`} onClick={() => chain().setTextAlign(a).run()}>{({ left: "左", center: "中", right: "右", justify: "两端" })[a]}</button>)}
        <button className="btn sm" onClick={() => chain().toggleBulletList().run()}>• 列表</button>
        <button className="btn sm" onClick={() => chain().toggleOrderedList().run()}>1. 列表</button>
        <span className="sep" style={{ width: 1, height: 20, background: "var(--line)" }} />
        <button className="btn sm primary" onClick={insertField}>＋ 插入字段</button>
        <button className="btn sm" onClick={() => body!.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}>▦ 表格</button>
        <button className={`btn sm${inBlock ? " active" : ""}`} onClick={toggleBlock} title="把选中的连续段落设为重复内容块（每条明细复制一份）">{inBlock ? "✓ 重复内容块" : "⟳ 重复内容块"}</button>
        <button className="btn sm" onClick={() => imgInput.current?.click()}>🖼 图片</button>
        <input ref={imgInput} type="file" accept="image/png,image/jpeg,image/gif" hidden onChange={(ev) => ev.target.files?.[0] && onImage(ev.target.files[0])} />
        <button className="btn sm" onClick={() => body!.chain().focus().insertContent({ type: "pageBreak" }).run()}>分页符</button>
        <button className="btn sm" onClick={() => chain().setHorizontalRule().run()}>分隔线</button>
        <button type="button" className="btn sm" aria-label="撤销" title="撤销" onClick={() => chain().undo().run()}>↶</button>
        <button type="button" className="btn sm" aria-label="重做" title="重做" onClick={() => chain().redo().run()}>↷</button>
      </div>
      {inTable && (
        <div className="toolbar small">
          表格：
          <button className="btn xs" onClick={() => body!.chain().focus().addRowAfter().run()}>下方插入行</button>
          <button className="btn xs" onClick={() => body!.chain().focus().deleteRow().run()}>删除行</button>
          <button className="btn xs" onClick={() => body!.chain().focus().addColumnAfter().run()}>右侧插入列</button>
          <button className="btn xs" onClick={() => body!.chain().focus().deleteColumn().run()}>删除列</button>
          <button className="btn xs" onClick={() => body!.chain().focus().mergeCells().run()}>合并单元格</button>
          <button className="btn xs" onClick={() => body!.chain().focus().splitCell().run()}>拆分</button>
          <button className="btn xs" onClick={() => body!.chain().focus().toggleHeaderRow().run()}>切换表头行</button>
          <input type="color" title="单元格底色" aria-label="单元格底色" onChange={(ev) => body!.chain().focus().setCellAttribute("background", ev.target.value).run()} style={{ width: 26, height: 22, border: 0 }} />
          <button className="btn xs" onClick={() => body!.chain().focus().deleteTable().run()}>删除表格</button>
          <span className="sep" style={{ width: 1, height: 18, background: "var(--line)" }} />
          <button className={`btn xs${rowAttrs.repeat ? " active" : ""}`} onClick={toggleRepeat}>{rowAttrs.repeat ? "✓ 重复明细行" : "设为重复明细行"}</button>
          {rowAttrs.repeat && (
            <>
              <input className="input sm" style={{ width: 110 }} value={rowAttrs.repeatName || ""} placeholder="明细名称" aria-label="明细名称" onChange={(ev) => body!.chain().updateAttributes("tableRow", { repeatName: ev.target.value }).run()} />
              <select className="input sm" aria-label="零条明细时" value={rowAttrs.emptyPolicy || "remove_row"} onChange={(ev) => body!.chain().updateAttributes("tableRow", { emptyPolicy: ev.target.value }).run()}>
                <option value="remove_row">零条：删除该行</option><option value="blank_row">零条：保留空行</option><option value="placeholder">零条：显示（无）</option><option value="remove_table">零条：删除表格</option>
              </select>
              <span className="tiny muted">行内的字段会成为明细列</span>
            </>
          )}
        </div>
      )}
      {inBlock && (
        <div className="toolbar small">
          重复内容块：
          <input className="input sm" style={{ width: 120 }} value={blockAttrs.repeatName || ""} placeholder="明细名称" onChange={(ev) => setBlock({ repeatName: ev.target.value })} aria-label="内容块名称" />
          <select className="input sm" value={blockAttrs.separator || "none"} onChange={(ev) => setBlock({ separator: ev.target.value })} aria-label="相邻两份之间">
            <option value="none">相邻：直接相连</option><option value="blank">相邻：空一行</option><option value="page_break">相邻：分页</option>
          </select>
          <select className="input sm" value={blockAttrs.emptyPolicy || "remove"} onChange={(ev) => setBlock({ emptyPolicy: ev.target.value })} aria-label="零条明细时">
            <option value="remove">零条：删除内容块</option><option value="placeholder">零条：显示（无）</option><option value="error">零条：视为错误</option>
          </select>
          <span className="tiny muted">块内插入的字段会成为明细列</span>
        </div>
      )}
      <div className="toolbar small">
        纸张
        <select className="input sm" aria-label="纸张大小" value={page.size} onChange={(ev) => { setPage({ ...page, size: ev.target.value }); setDirty(true); }}>{Object.keys(PAPERS).map((k) => <option key={k}>{k}</option>)}</select>
        <select className="input sm" aria-label="纸张方向" value={page.orientation} onChange={(ev) => { setPage({ ...page, orientation: ev.target.value }); setDirty(true); }}><option value="portrait">纵向</option><option value="landscape">横向</option></select>
        页边距（毫米）
        {(["top", "bottom", "left", "right"] as const).map((k) => (
          <label key={k} className="row gap4">{({ top: "上", bottom: "下", left: "左", right: "右" })[k]}<input className="input sm" type="number" style={{ width: 58 }} value={m[k]} onChange={(ev) => { setPage({ ...page, margins: { ...m, [k]: Number(ev.target.value) } }); setDirty(true); }} /></label>
        ))}
      </div>
      <div className="editor-body">
        <div className="editor-page" style={{ width: W, padding: `${m.top * 3.78 * 0.6}px ${m.right * 3.78}px ${m.bottom * 3.78 * 0.6}px ${m.left * 3.78}px`, minHeight: 600 }}>
          <div className="hf-box"><div className="tiny muted">页眉</div><EditorContent editor={header} /></div>
          <EditorContent editor={body} />
          <div className="hf-box" style={{ marginTop: 24 }}><div className="tiny muted">页脚</div><EditorContent editor={footer} /></div>
        </div>
        <div className="tiny muted" style={{ textAlign: "center", marginTop: 10 }}>编辑区为近似排版；实际分页以 Word / LibreOffice 打开 DOCX 为准。</div>
      </div>
    </div>
  );
}
