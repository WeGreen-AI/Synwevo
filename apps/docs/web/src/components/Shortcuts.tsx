import { Modal } from "@synwevo/ui";

const GROUPS: { title: string; items: [string, string][] }[] = [
  {
    title: "全局",
    items: [
      ["⌘K / Ctrl+K", "命令面板（所有操作）"],
      ["?", "显示本帮助（单键快捷键）"],
      ["⌘Z / Ctrl+Z", "撤销上一步（映射、模板字段）"],
      ["⌘⇧Z / Ctrl+Shift+Z", "重做"],
      ["Esc", "清除选择 / 关闭对话框"],
      ["⌘F / Ctrl+F", "在当前来源文件中查找"],
    ],
  },
  {
    title: "映射列表（列表获得焦点时）",
    items: [
      ["↑ / ↓、J / K", "在映射之间移动"],
      ["Enter", "编辑当前映射"],
      ["X / 空格", "选中 / 取消选中（可多选）"],
      ["Shift + ↑ / ↓", "连续范围多选"],
      ["⌘A / Ctrl+A", "全选映射"],
      ["D", "启用 / 禁用选中的映射"],
      ["Delete / Backspace", "删除选中的映射（可撤销）"],
      ["Esc", "取消多选"],
    ],
  },
  {
    title: "文档与表格",
    items: [
      ["拖动 / Shift+点击", "选择文字、单元格区域"],
      ["方向键 / Shift+方向键", "表格获得焦点时移动 / 扩展选区"],
      ["Alt/Option + 点击", "在文档中放置插入光标（不替换原文）"],
      ["来源文件列表：Alt/Option + ↑ / ↓", "调整当前文件的顺序"],
      ["分隔条：← / →（Shift 加大步长）", "调整宽度；中间分隔条 Enter 恢复默认，右侧面板分隔条 Enter 收起"],
    ],
  },
  {
    title: "生成",
    items: [
      ["⌘Enter / Ctrl+Enter", "开始批量生成"],
      ["P", "打开 / 关闭预览（单键快捷键）"],
    ],
  },
];

/** Keyboard help. Only shortcuts that exist are listed (⌘+/⌘-, 0 and "G then P" were listed but
 *  never implemented — ⌘+/- is the browser's own zoom and stays that way).
 *
 *  WCAG 2.1.4: single-character shortcuts (P, ?) must be possible to turn off, because speech
 *  input users trigger them by accident. The switch lives here and is remembered per browser. */
export default function Shortcuts({ onClose, singleKeys, onSingleKeys }: { onClose: () => void; singleKeys: boolean; onSingleKeys: (v: boolean) => void }) {
  return (
    <Modal title="键盘快捷键" size="wide" onClose={onClose}
      footer={<><span className="tiny muted">输入框、编辑器与中文输入法输入时，字母快捷键不会触发。</span><span className="spacer" /><button type="button" className="btn primary" onClick={onClose}>知道了</button></>}>
      <label className="check small mb12">
        <input type="checkbox" checked={singleKeys} onChange={(e) => onSingleKeys(e.target.checked)} />
        启用单键快捷键（P、?）。使用语音输入或经常误触时可以关闭；带 ⌘ / Ctrl 的快捷键不受影响。
      </label>
      <div className="grid2">
        {GROUPS.map((g) => (
          <div key={g.title} className="col gap4">
            <h3>{g.title}</h3>
            <table className="tbl small">
              <tbody>
                {g.items.map(([k, d]) => (
                  <tr key={k}><td style={{ width: 180 }}><kbd className="kbd">{k}</kbd></td><td>{d}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </Modal>
  );
}
