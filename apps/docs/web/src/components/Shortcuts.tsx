import { Modal } from "@synwevo/ui";

const GROUPS: { title: string; items: [string, string][] }[] = [
  {
    title: "全局",
    items: [
      ["⌘K / Ctrl+K", "命令面板（所有操作）"],
      ["?", "显示本帮助"],
      ["⌘Z / Ctrl+Z", "撤销上一步（映射、模板字段）"],
      ["⌘⇧Z / Ctrl+Shift+Z", "重做"],
      ["Esc", "清除选择 / 关闭对话框"],
      ["⌘F / Ctrl+F", "在当前来源文件中查找"],
      ["G 然后 P", "预览；再按一次返回模板"],
    ],
  },
  {
    title: "映射列表",
    items: [
      ["↑ / ↓、J / K", "在映射之间移动"],
      ["Enter", "编辑当前映射"],
      ["X", "选中 / 取消选中（可多选）"],
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
      ["Alt/Option + 点击", "在文档中放置插入光标（不替换原文）"],
      ["⌘+ / ⌘-（Ctrl）", "放大 / 缩小当前一侧"],
      ["0", "缩放恢复为适合宽度"],
    ],
  },
  {
    title: "生成",
    items: [
      ["⌘Enter / Ctrl+Enter", "开始批量生成"],
      ["P", "打开预览"],
    ],
  },
];

export default function Shortcuts({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="键盘快捷键" size="wide" onClose={onClose}
      footer={<><span className="tiny muted">输入框、编辑器与中文输入法输入时，字母快捷键不会触发。</span><span className="spacer" /><button className="btn primary" onClick={onClose}>知道了</button></>}>
      <div className="grid2">
        {GROUPS.map((g) => (
          <div key={g.title} className="col gap4">
            <h3>{g.title}</h3>
            <table className="tbl small">
              <tbody>
                {g.items.map(([k, d]) => (
                  <tr key={k}><td style={{ width: 160 }}><kbd className="kbd">{k}</kbd></td><td>{d}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </Modal>
  );
}
