# Agent 预算界面验收 · 2026-10-08

绑定 verification.json 的源文件 SHA-256；基线 5ffec9d。使用真实组合服务、隔离 Store 与明确合成的钱包/用户，127.0.0.1:33871。上游 transport 与最终任务启动均禁止；没有真实模型调用、扣费或发送。

- 新预算调用次数默认6，金额仍为空，不能自动开始。键盘改为1后真实报价保留1次及0.001金额；没有提高确认范围。
- 简体中文、英文、繁体中文均展示次数与金额分别限制的说明；既有用户内容不变。
- Chrome真实390×844设备视口下长对话框无横向溢出；Tab可到全部输入/操作，Escape关闭后焦点回发送按钮。
- prefers-reduced-motion: reduce下操作可用；结束恢复No emulation。截图已在本任务浏览器工具结果中检查，未另保存PNG。
- 服务运行期间Console为0 messages、3 possible improvements；没有把改进提示写成零问题。
- 原生自动化setValue最初清空数字输入，服务端正确拒绝空值；改用键盘输入后通过，未改产品验证。
- 验收服务已正常shutdown；其后标签页轮询产生ERR_CONNECTION_REFUSED，属于停服后的清理阶段，随后已关闭本轮本地标签页，保留生产Agent/Admin页面。

生产已有调用只做脱敏元数据复核，见existing-live-read-call.json。个人支出保持暂停。此证据不替代生产模型→新产物、跨空间或真实付款验收。
