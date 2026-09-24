import { serverText, issueText } from "../lib/presentation";
import { t as tr, useI18n, LanguageMenu } from "@synwevo/ui";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { EditorContent, Node, mergeAttributes, useEditor, Editor, ReactNodeViewRenderer, NodeViewWrapper, NodeViewContent, type NodeViewProps } from "@tiptap/react";
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

/** The editor chrome uses live React views; serialized node attributes and document JSON stay unchanged. */
function FieldView({ node }: NodeViewProps) {
  const { t } = useI18n();
  return <NodeViewWrapper as="span" data-field={node.attrs.id} className={`field-node${node.attrs.required ? " req" : ""}`} title={t("字段：{v1}", { v1: node.attrs.name })} contentEditable={false}>【{node.attrs.name}】</NodeViewWrapper>;
}
function RepeatBlockView({ node }: NodeViewProps) {
  const { t } = useI18n();
  return <NodeViewWrapper data-repeat-block={node.attrs.repeatId || ""} className="repeat-block" data-label={`⟳ ${t("重复内容块：")}${node.attrs.repeatName || ""}`}><NodeViewContent /></NodeViewWrapper>;
}
const Field = Node.create({
  addNodeView() { return ReactNodeViewRenderer(FieldView); },
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
  addNodeView() { return ReactNodeViewRenderer(RepeatBlockView); },
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
  useI18n();
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
      if (t.kind !== "builtin_doc") { setLoadErr(tr("这个模板不是用文档编辑器创建的，不能在这里编辑。")); return; }
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
    if (dirty && !(await confirm(tr("放弃未保存的修改？"), tr("模板有尚未保存的修改，离开后这些修改会丢失。"), tr("放弃修改并离开"), true))) return;
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
    const n = await prompt(tr("插入字段"), tr("字段名称（生成时替换为数据）"), "");
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
      toast(tr("请先把光标放在表格的某一行中"));
      return;
    }
    const cur = e.getAttributes("tableRow");
    if (cur.repeat) {
      e.chain().focus().updateAttributes("tableRow", { repeat: false, repeatId: null }).run();
    } else {
      e.chain().focus().updateAttributes("tableRow", { repeat: true, repeatId: cur.repeatId || rid(), repeatName: cur.repeatName || "明细" }).run();
      toast(tr("已设为重复明细行：行内插入的字段即明细列；生成时每条明细复制一行"));
    }
  };

  const inBlock = body?.isActive("repeatBlock");
  const blockAttrs = inBlock ? body!.getAttributes("repeatBlock") : {};
  const toggleBlock = () => {
    const e = body!;
    if (e.isActive("table")) {
      toast(tr("表格中请使用「设为重复明细行」；重复内容块用于表格以外的段落"));
      return;
    }
    if (e.isActive("repeatBlock")) {
      e.chain().focus().lift("repeatBlock").run();
      toast(tr("已取消重复内容块（段落保留）"));
      return;
    }
    if (!e.chain().focus().wrapIn("repeatBlock", { repeatId: rid(), repeatName: "明细" }).run()) {
      toast(tr("无法把选中的内容设为重复内容块（请选中表格以外的连续段落）"), "err");
      return;
    }
    toast(tr("已设为重复内容块：块内插入的字段就是明细列；生成时每条明细复制一份这些段落"));
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
          baseVersion.current, (theirs, mine) => confirm(tr(STALE_TITLE), staleText(theirs, mine), tr("仍然保存"), true));
        if (!t) { toast(tr(STALE_KEPT), "info"); return; }
      } else t = await post("/api/templates/builtin", { kind: "builtin_doc", name, editor, project_id: projectId });
      baseVersion.current = loadedVersion(t);
      setDirty(false);
      toast(tr("已保存（{v1} 个字段，v{v2}）", { v1: t.version.slots.length, v2: t.version.no }), "ok");
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
      <div className="big">{tr("无法打开模板")}</div>
      <div className="notice err" style={{ maxWidth: 480 }}>{serverText(loadErr)}</div>
      <button type="button" className="btn" onClick={() => nav(projectId ? `/p/${projectId}` : "/")}>{tr("返回")}</button>
    </div>
  );

  return (
    <div className="editor-shell" inert={busy || !loaded}>
      <div className="topbar">
        <LanguageMenu />
        <button type="button" className="btn ghost sm" onClick={back}>{tr("← 返回")}</button>
        <input className="input" value={name} onChange={(ev) => { setName(ev.target.value); setDirty(true); }} style={{ width: 240, fontWeight: 600 }} aria-label={tr("模板名称")} />
        <span className="badge">{tr("文档型模板 → DOCX")}</span>
        {!loaded ? <span className="badge info" role="status">{tr("加载中…")}</span> : dirty ? <span className="badge warn" role="status">{tr("未保存")}</span> : <span className="badge ok" role="status">{tr("已保存")}</span>}
        <span className="spacer" />
        <button type="button" className="btn primary" disabled={busy || !loaded} onClick={saveTpl}>{busy ? tr("保存中…") : tr("保存模板")}</button>
      </div>
      <div className="toolbar">
        <select className="input sm" aria-label={tr("段落样式")} value={e?.isActive("heading", { level: 1 }) ? "h1" : e?.isActive("heading", { level: 2 }) ? "h2" : e?.isActive("heading", { level: 3 }) ? "h3" : "p"}
          onChange={(ev) => { const v = ev.target.value; v === "p" ? chain().setParagraph().run() : chain().setHeading({ level: Number(v[1]) as any }).run(); }}>
          <option value="p">{tr("正文")}</option><option value="h1">{tr("标题 1")}</option><option value="h2">{tr("标题 2")}</option><option value="h3">{tr("标题 3")}</option>
        </select>
        <select className="input sm" aria-label={tr("字号")} onChange={(ev) => { ev.target.value ? chain().setFontSize(ev.target.value).run() : chain().unsetFontSize().run(); }} defaultValue="">
          <option value="">{tr("字号")}</option>{["9pt", "10pt", "10.5pt", "12pt", "14pt", "16pt", "18pt", "22pt", "26pt"].map((x) => <option key={x}>{x}</option>)}
        </select>
        <select className="input sm" aria-label={tr("字体")} onChange={(ev) => { ev.target.value ? chain().setFontFamily(ev.target.value).run() : chain().unsetFontFamily().run(); }} defaultValue="">
          <option value="">{tr("字体")}</option>{["宋体", "黑体", "楷体", "仿宋", "微软雅黑", "Arial", "Times New Roman"].map((x) => <option key={x} value={x}>{tr(x)}</option>)}
        </select>
        <button type="button" className={`btn sm${e?.isActive("bold") ? " active" : ""}`} aria-pressed={!!e?.isActive("bold")} aria-label={tr("加粗")} title={tr("加粗")} onClick={() => chain().toggleBold().run()}><b>B</b></button>
        <button type="button" className={`btn sm${e?.isActive("italic") ? " active" : ""}`} aria-pressed={!!e?.isActive("italic")} aria-label={tr("斜体")} title={tr("斜体")} onClick={() => chain().toggleItalic().run()}><i>I</i></button>
        <button type="button" className={`btn sm${e?.isActive("underline") ? " active" : ""}`} aria-pressed={!!e?.isActive("underline")} aria-label={tr("下划线")} title={tr("下划线")} onClick={() => chain().toggleUnderline().run()}><u>U</u></button>
        <button type="button" className={`btn sm${e?.isActive("strike") ? " active" : ""}`} aria-pressed={!!e?.isActive("strike")} aria-label={tr("删除线")} title={tr("删除线")} onClick={() => chain().toggleStrike().run()}><s>S</s></button>
        <input type="color" title={tr("文字颜色")} aria-label={tr("文字颜色")} onChange={(ev) => chain().setColor(ev.target.value).run()} style={{ width: 28, height: 26, border: 0, background: "none" }} />
        <button type="button" className={`btn sm${e?.isActive("highlight") ? " active" : ""}`} aria-pressed={!!e?.isActive("highlight")} aria-label={tr("高亮")} onClick={() => chain().toggleHighlight().run()} title={tr("高亮")}>▇</button>
        <span className="sep" style={{ width: 1, height: 20, background: "var(--line)" }} />
        {(["left", "center", "right", "justify"] as const).map((a) => <button key={a} className={`btn sm${e?.isActive({ textAlign: a }) ? " active" : ""}`} onClick={() => chain().setTextAlign(a).run()}>{({ left: tr("左"), center: tr("中"), right: tr("右"), justify: tr("两端") })[a]}</button>)}
        <button className="btn sm" onClick={() => chain().toggleBulletList().run()}>{tr("• 列表")}</button>
        <button className="btn sm" onClick={() => chain().toggleOrderedList().run()}>{tr("1. 列表")}</button>
        <span className="sep" style={{ width: 1, height: 20, background: "var(--line)" }} />
        <button className="btn sm primary" onClick={insertField}>{tr("＋ 插入字段")}</button>
        <button className="btn sm" onClick={() => body!.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}>{tr("▦ 表格")}</button>
        <button className={`btn sm${inBlock ? " active" : ""}`} onClick={toggleBlock} title={tr("把选中的连续段落设为重复内容块（每条明细复制一份）")}>{inBlock ? tr("✓ 重复内容块") : tr("⟳ 重复内容块")}</button>
        <button className="btn sm" onClick={() => imgInput.current?.click()}>{tr("🖼 图片")}</button>
        <input ref={imgInput} type="file" accept="image/png,image/jpeg,image/gif" hidden onChange={(ev) => ev.target.files?.[0] && onImage(ev.target.files[0])} />
        <button className="btn sm" onClick={() => body!.chain().focus().insertContent({ type: "pageBreak" }).run()}>{tr("分页符")}</button>
        <button className="btn sm" onClick={() => chain().setHorizontalRule().run()}>{tr("分隔线")}</button>
        <button type="button" className="btn sm" aria-label={tr("撤销")} title={tr("撤销")} onClick={() => chain().undo().run()}>↶</button>
        <button type="button" className="btn sm" aria-label={tr("重做")} title={tr("重做")} onClick={() => chain().redo().run()}>↷</button>
      </div>
      {inTable && (
        <div className="toolbar small">{tr("表格：")}<button className="btn xs" onClick={() => body!.chain().focus().addRowAfter().run()}>{tr("下方插入行")}</button>
          <button className="btn xs" onClick={() => body!.chain().focus().deleteRow().run()}>{tr("删除行")}</button>
          <button className="btn xs" onClick={() => body!.chain().focus().addColumnAfter().run()}>{tr("右侧插入列")}</button>
          <button className="btn xs" onClick={() => body!.chain().focus().deleteColumn().run()}>{tr("删除列")}</button>
          <button className="btn xs" onClick={() => body!.chain().focus().mergeCells().run()}>{tr("合并单元格")}</button>
          <button className="btn xs" onClick={() => body!.chain().focus().splitCell().run()}>{tr("拆分")}</button>
          <button className="btn xs" onClick={() => body!.chain().focus().toggleHeaderRow().run()}>{tr("切换表头行")}</button>
          <input type="color" title={tr("单元格底色")} aria-label={tr("单元格底色")} onChange={(ev) => body!.chain().focus().setCellAttribute("background", ev.target.value).run()} style={{ width: 26, height: 22, border: 0 }} />
          <button className="btn xs" onClick={() => body!.chain().focus().deleteTable().run()}>{tr("删除表格")}</button>
          <span className="sep" style={{ width: 1, height: 18, background: "var(--line)" }} />
          <button className={`btn xs${rowAttrs.repeat ? " active" : ""}`} onClick={toggleRepeat}>{rowAttrs.repeat ? tr("✓ 重复明细行") : tr("设为重复明细行")}</button>
          {rowAttrs.repeat && (
            <>
              <input className="input sm" style={{ width: 110 }} value={rowAttrs.repeatName || ""} placeholder={tr("明细名称")} aria-label={tr("明细名称")} onChange={(ev) => body!.chain().updateAttributes("tableRow", { repeatName: ev.target.value }).run()} />
              <select className="input sm" aria-label={tr("零条明细时")} value={rowAttrs.emptyPolicy || "remove_row"} onChange={(ev) => body!.chain().updateAttributes("tableRow", { emptyPolicy: ev.target.value }).run()}>
                <option value="remove_row">{tr("零条：删除该行")}</option><option value="blank_row">{tr("零条：保留空行")}</option><option value="placeholder">{tr("零条：显示（无）")}</option><option value="remove_table">{tr("零条：删除表格")}</option>
              </select>
              <span className="tiny muted">{tr("行内的字段会成为明细列")}</span>
            </>
          )}
        </div>
      )}
      {inBlock && (
        <div className="toolbar small">{tr("重复内容块：")}<input className="input sm" style={{ width: 120 }} value={blockAttrs.repeatName || ""} placeholder={tr("明细名称")} onChange={(ev) => setBlock({ repeatName: ev.target.value })} aria-label={tr("内容块名称")} />
          <select className="input sm" value={blockAttrs.separator || "none"} onChange={(ev) => setBlock({ separator: ev.target.value })} aria-label={tr("相邻两份之间")}>
            <option value="none">{tr("相邻：直接相连")}</option><option value="blank">{tr("相邻：空一行")}</option><option value="page_break">{tr("相邻：分页")}</option>
          </select>
          <select className="input sm" value={blockAttrs.emptyPolicy || "remove"} onChange={(ev) => setBlock({ emptyPolicy: ev.target.value })} aria-label={tr("零条明细时")}>
            <option value="remove">{tr("零条：删除内容块")}</option><option value="placeholder">{tr("零条：显示（无）")}</option><option value="error">{tr("零条：视为错误")}</option>
          </select>
          <span className="tiny muted">{tr("块内插入的字段会成为明细列")}</span>
        </div>
      )}
      <div className="toolbar small">{tr("纸张")}<select className="input sm" aria-label={tr("纸张大小")} value={page.size} onChange={(ev) => { setPage({ ...page, size: ev.target.value }); setDirty(true); }}>{Object.keys(PAPERS).map((k) => <option key={k}>{k}</option>)}</select>
        <select className="input sm" aria-label={tr("纸张方向")} value={page.orientation} onChange={(ev) => { setPage({ ...page, orientation: ev.target.value }); setDirty(true); }}><option value="portrait">{tr("纵向")}</option><option value="landscape">{tr("横向")}</option></select>{tr("页边距（毫米）")}{(["top", "bottom", "left", "right"] as const).map((k) => (
          <label key={k} className="row gap4">{({ top: tr("上"), bottom: tr("下"), left: tr("左"), right: tr("右") })[k]}<input className="input sm" type="number" style={{ width: 58 }} value={m[k]} onChange={(ev) => { setPage({ ...page, margins: { ...m, [k]: Number(ev.target.value) } }); setDirty(true); }} /></label>
        ))}
      </div>
      <div className="editor-body">
        <div className="editor-page" style={{ width: W, padding: `${m.top * 3.78 * 0.6}px ${m.right * 3.78}px ${m.bottom * 3.78 * 0.6}px ${m.left * 3.78}px`, minHeight: 600 }}>
          <div className="hf-box"><div className="tiny muted">{tr("页眉")}</div><EditorContent editor={header} /></div>
          <EditorContent editor={body} />
          <div className="hf-box" style={{ marginTop: 24 }}><div className="tiny muted">{tr("页脚")}</div><EditorContent editor={footer} /></div>
        </div>
        <div className="tiny muted" style={{ textAlign: "center", marginTop: 10 }}>{tr("编辑区为近似排版；实际分页以 Word / LibreOffice 打开 DOCX 为准。")}</div>
      </div>
    </div>
  );
}
