import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { EditorContent, Node, mergeAttributes, useEditor, Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Table, TableCell, TableHeader, TableRow } from "@tiptap/extension-table";
import { TextStyle, Color, FontSize, FontFamily } from "@tiptap/extension-text-style";
import TextAlign from "@tiptap/extension-text-align";
import Image from "@tiptap/extension-image";
import Highlight from "@tiptap/extension-highlight";
import { get, post, put } from "../lib/api";
import { useDialog, useToast } from "../lib/ui";

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

const CellBg = (Base: any) => Base.extend({
  addAttributes() {
    return { ...this.parent?.(), background: { default: null, renderHTML: (a: any) => (a.background ? { style: `background:${a.background}` } : {}) } };
  },
});

const extensions = (placeholder?: string) => [
  StarterKit.configure({ link: false } as any),
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
  const { prompt } = useDialog();
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
  });
  const body = useEditor(mk(EMPTY));
  const header = useEditor(mk(EMPTY));
  const footer = useEditor(mk(EMPTY));

  useEffect(() => {
    if (!tid || !body || !header || !footer) return;
    get(`/api/templates/${tid}`).then((t) => {
      setName(t.name);
      const ed = t.version.editor || {};
      if (ed.page) setPage(ed.page);
      body.commands.setContent(ed.body || EMPTY);
      header.commands.setContent(ed.header || EMPTY);
      footer.commands.setContent(ed.footer || EMPTY);
      setLoaded(true);
      setDirty(false);
    }).catch((e) => toast(e.message, "err"));
  }, [tid, body, header, footer]);

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); } };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const ed = () => focused.current || body!;
  const chain = () => ed().chain().focus();

  const insertField = async () => {
    const n = await prompt("插入字段", "字段名称（生成时替换为数据）", "");
    if (!n) return;
    chain().insertContent({ type: "field", attrs: { id: rid(), name: n.trim(), required: false } }).run();
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

  const onImage = (f: File) => {
    const r = new FileReader();
    r.onload = () => {
      const img = new window.Image();
      img.onload = () => {
        const w = Math.min(img.width, 600);
        chain().setImage({ src: String(r.result), width: w } as any).run();
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
      if (tid) t = await put(`/api/templates/${tid}/builtin`, { editor, name });
      else t = await post("/api/templates/builtin", { kind: "builtin_doc", name, editor, project_id: projectId });
      setDirty(false);
      toast(`已保存（${t.version.slots.length} 个字段，v${t.version.no}）`, "ok");
      if (!tid) nav(`/templates/${t.id}/edit${projectId ? `?project=${projectId}` : ""}`, { replace: true });
    } catch (e: any) {
      toast(e.message, "err");
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

  return (
    <div className="editor-shell">
      <div className="topbar">
        <a className="btn ghost sm" onClick={() => (projectId ? nav(`/p/${projectId}`) : nav("/"))}>← 返回</a>
        <input className="input" value={name} onChange={(ev) => { setName(ev.target.value); setDirty(true); }} style={{ width: 240, fontWeight: 600 }} aria-label="模板名称" />
        <span className="badge">文档型模板 → DOCX</span>
        {dirty ? <span className="badge warn">未保存</span> : <span className="badge ok">已保存</span>}
        <span className="spacer" />
        <button className="btn primary" disabled={busy || !loaded} onClick={saveTpl}>{busy ? "保存中…" : "保存模板"}</button>
      </div>
      <div className="toolbar">
        <select className="input sm" value={e?.isActive("heading", { level: 1 }) ? "h1" : e?.isActive("heading", { level: 2 }) ? "h2" : e?.isActive("heading", { level: 3 }) ? "h3" : "p"}
          onChange={(ev) => { const v = ev.target.value; v === "p" ? chain().setParagraph().run() : chain().setHeading({ level: Number(v[1]) as any }).run(); }}>
          <option value="p">正文</option><option value="h1">标题 1</option><option value="h2">标题 2</option><option value="h3">标题 3</option>
        </select>
        <select className="input sm" onChange={(ev) => { ev.target.value ? chain().setFontSize(ev.target.value).run() : chain().unsetFontSize().run(); }} defaultValue="">
          <option value="">字号</option>{["9pt", "10pt", "10.5pt", "12pt", "14pt", "16pt", "18pt", "22pt", "26pt"].map((x) => <option key={x}>{x}</option>)}
        </select>
        <select className="input sm" onChange={(ev) => { ev.target.value ? chain().setFontFamily(ev.target.value).run() : chain().unsetFontFamily().run(); }} defaultValue="">
          <option value="">字体</option>{["宋体", "黑体", "楷体", "仿宋", "微软雅黑", "Arial", "Times New Roman"].map((x) => <option key={x}>{x}</option>)}
        </select>
        <button className={`btn sm${e?.isActive("bold") ? " active" : ""}`} onClick={() => chain().toggleBold().run()}><b>B</b></button>
        <button className={`btn sm${e?.isActive("italic") ? " active" : ""}`} onClick={() => chain().toggleItalic().run()}><i>I</i></button>
        <button className={`btn sm${e?.isActive("underline") ? " active" : ""}`} onClick={() => chain().toggleUnderline().run()}><u>U</u></button>
        <button className={`btn sm${e?.isActive("strike") ? " active" : ""}`} onClick={() => chain().toggleStrike().run()}><s>S</s></button>
        <input type="color" title="文字颜色" onChange={(ev) => chain().setColor(ev.target.value).run()} style={{ width: 28, height: 26, border: 0, background: "none" }} />
        <button className={`btn sm${e?.isActive("highlight") ? " active" : ""}`} onClick={() => chain().toggleHighlight().run()} title="高亮">▇</button>
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
        <button className="btn sm" onClick={() => chain().undo().run()}>↶</button>
        <button className="btn sm" onClick={() => chain().redo().run()}>↷</button>
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
          <input type="color" title="单元格底色" onChange={(ev) => body!.chain().focus().setCellAttribute("background", ev.target.value).run()} style={{ width: 26, height: 22, border: 0 }} />
          <button className="btn xs" onClick={() => body!.chain().focus().deleteTable().run()}>删除表格</button>
          <span className="sep" style={{ width: 1, height: 18, background: "var(--line)" }} />
          <button className={`btn xs${rowAttrs.repeat ? " active" : ""}`} onClick={toggleRepeat}>{rowAttrs.repeat ? "✓ 重复明细行" : "设为重复明细行"}</button>
          {rowAttrs.repeat && (
            <>
              <input className="input sm" style={{ width: 110 }} value={rowAttrs.repeatName || ""} placeholder="明细名称" onChange={(ev) => body!.chain().updateAttributes("tableRow", { repeatName: ev.target.value }).run()} />
              <select className="input sm" value={rowAttrs.emptyPolicy || "remove_row"} onChange={(ev) => body!.chain().updateAttributes("tableRow", { emptyPolicy: ev.target.value }).run()}>
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
        <select className="input sm" value={page.size} onChange={(ev) => { setPage({ ...page, size: ev.target.value }); setDirty(true); }}>{Object.keys(PAPERS).map((k) => <option key={k}>{k}</option>)}</select>
        <select className="input sm" value={page.orientation} onChange={(ev) => { setPage({ ...page, orientation: ev.target.value }); setDirty(true); }}><option value="portrait">纵向</option><option value="landscape">横向</option></select>
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
