/** How a change-log row reads: which part and block, what happened, and the block's text before
 *  and after as insertions and deletions (9.13 D). Shared by the document editor's change log and
 *  the project page's version dialog, so a row reads the same wherever it appears. */
import { t as tr } from "@synwevo/ui";
import { charDiff } from "./diff";

const PART_LABEL: Record<string, () => string> = { body: () => tr("正文"), header: () => tr("页眉"), footer: () => tr("页脚") };
const NODE_LABEL: Record<string, () => string> = {
  paragraph: () => tr("段落"), heading: () => tr("标题"), table: () => tr("表格"), bulletList: () => tr("列表"), orderedList: () => tr("列表"),
  image: () => tr("图片"), repeatBlock: () => tr("重复内容块"), pageBreak: () => tr("分页符"), horizontalRule: () => tr("分隔线"), blockquote: () => tr("引用"),
};
/** What the block said before and after, as insertions and deletions the reader can see (9.13 D):
 *  an edit row carries the block's text from before the run of keystrokes and the excerpt after it;
 *  an inserted block is all new, a deleted block all gone. <ins>/<del> carry the meaning for a
 *  screen reader, the colours only underline it. */
export function ChangeExcerpt({ item }: { item: any }) {
  if (item.kind === "insert") return <span className="excerpt"><ins className="diff-ins" title={tr("新增的内容")}>{item.excerpt}</ins></span>;
  if (item.kind === "delete") return <span className="excerpt"><del className="diff-del" title={tr("删除的内容")}>{item.excerpt}</del></span>;
  if (item.kind === "edit" && typeof item.before === "string" && item.before !== item.excerpt) {
    const runs = charDiff(item.before, item.excerpt);
    return (
      <span className="excerpt" data-testid="change-diff">
        {runs.map((r, i) => r.kind === "equal" ? <span key={i}>{r.text}</span>
          : r.kind === "ins" ? <ins key={i} className="diff-ins" title={tr("新增的内容")}>{r.text}</ins>
          : <del key={i} className="diff-del" title={tr("删除的内容")}>{r.text}</del>)}
      </span>
    );
  }
  return <span className="excerpt">{item.excerpt}</span>;
}
export function changeText(item: any): string {
  const part = PART_LABEL[item.part]?.() || item.part;
  const node = NODE_LABEL[item.node_type]?.() || tr("内容");
  const n = (item.block_no ?? 0) + 1;
  switch (item.kind) {
    case "edit": return tr("修改了{v1}第 {v2} 个{v3}", { v1: part, v2: n, v3: node });
    case "insert": return tr("在{v1}第 {v2} 处新增了{v3}", { v1: part, v2: n, v3: node });
    case "delete": return tr("删除了{v1}第 {v2} 个{v3}", { v1: part, v2: n, v3: node });
    case "restore": return tr("把模板恢复为 {v1}（编辑内容随之更新）", { v1: item.excerpt });
    case "replace": return tr("在编辑器之外保存了 {v1}（编辑内容随之更新）", { v1: item.excerpt });
    default: return item.kind;
  }
}
export function ago(ts: number): string {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return tr("刚刚");
  if (s < 3600) return tr("{v1} 分钟前", { v1: Math.floor(s / 60) });
  if (s < 86400) return tr("{v1} 小时前", { v1: Math.floor(s / 3600) });
  return new Date(ts * 1000).toLocaleString();
}

