# Admin A27 注册诊断证据（CR-097）

`verification.json` 与 `browser-report.json` 记录 2026-10-11 在隔离合成实例（`127.0.0.1:33937`，邮件为 file 模式，
没有外发）上的浏览器验收；四张 `a27-diagnosis-*.png` 是同一次运行的截图。

**redacted 2026-10-25：** 截图里注册用的地址取了真实机构域名下的学号格式（`1155NNNNNN@link.cuhk.edu.hk`），
随机生成的号码仍可能属于真实的人。四张截图中这个地址出现的三处（账户列表第一行、诊断输入框、诊断结果的
「地址」行）已用不透明黑框覆盖；画面其余部分未改动，也没有重新截图。文本记录里该地址本来就只以
`NNNNNNNNNN@…` 的掩码形式出现。今后的验收用保留域名（例如 `1155000000@link.example.test`，单元测试已在用）。

**已知缺陷（原始采集就有，2026-10-25 记下）：** `a27-diagnosis-activated-zh-hans.png` 与 `a27-diagnosis-pending-zh-hans.png` 逐字节相同（涂黑前两者的 SHA-1 都是 `e69229…`，涂黑后都是 `4f9981…`）：它显示的是激活**前**的画面（账户行「待激活」），不是激活后的状态。激活后的状态只有两处证据：`verification.json` 的 `browser_steps.activated.stage_rows`（账户「正常」、激活「已激活」）与英文截图 `a27-diagnosis-en.png`（账户行 Active）。简体中文的激活后截图没有留下，也没有补拍。
