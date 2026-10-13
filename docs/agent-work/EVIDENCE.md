# 业务应用与个人 Agent：证据

每条注明提交、命令或路径、结果。只记录实际运行过的检查；未运行的写“未运行”。

## 2026-09-29 首轮现场核查（主检出 `main` @ 8266035）

* Git：remote `git@github.com:WeGreen-AI/Synwevo.git`；工作树有另一会话（API 平台 API-4 OAuth 同意）的未提交修改，
  本专项不触碰；同目录另有运行中的会话「Synwevo 平台统一体系与完整交付」（管理员平台主线）。
* 部署：线上 aadcb9e（2026-09-28 12:47 UTC，run 36421027638）；此后 `main` 的运行都被 GitHub Actions 账单拒绝
  （账单/支出限制；具体恢复时间未核实，不以月初推断已解除）。
* 部署机制：`.github/workflows/production.yml` verify → deploy（`git archive` 经受限 SSH 送到 `receive.py`，主机构建
  镜像、备份并校验、`compose up --wait`、`/readyz`）。`nginx.conf` 与 `compose.yaml` 不随部署下发。
* 模型提供方：仓库无任何模型调用；`.venv` 无 `anthropic`/`openai`；本机无产品可用的模型密钥。
* OCR：Docs 内的 Tesseract 子进程（`apps/docs/synwevo_docs/engine/ocr.py`，chi_sim+eng，单词归一化坐标）。
* 编排：不存在通用流程引擎（调查结论，代码核对）。

## 2026-09-30 WP0 + WP1（功能分支 `feat/business-apps` @ 51614b4，worktree `.claude/worktrees/business-apps`）

* 全量回归（worktree 代码，PYTHONPATH 指向 worktree；四个外壳全部构建）：2 905 项收集，2 项跳过，0 失败/错误
  （`pytest -q`，在最后一次 Python 改动之后运行）。其间发现并修复：单源站只构建了新外壳时回落到不存在的外壳返回 500
  （改为 404）；`filling.upload` 写 blob 未登记；`starlette` 未声明；`platform.py test` 新套件。
* 新套件 `apps/suite/tests`：能力层 15 项（显式空间、角色、幂等冲突与并发、事务回滚记失败、人工决定不可自动化、
  深度上限、个人操作、未声明事件、停用应用、两阶段运行转 `unknown`、HTTP 与 CSRF、目录）、Forms 11 项（版本固定、
  三个独立请求、隐藏字段清空与金额精度、草稿≠提交、补件修订、到期/撤销/换链接、公开链接填写人隔离与附件归属、
  附件类型/大小/导出权限、要求登录的专属链接、跨空间隔离与复制不带数据、请求幂等）、共享规则向量 10 项（Python 与
  浏览器两侧各跑一遍，均通过）。集成：`test_suite_mount.py` 7 项（portal/all 提供，app/accounts/messages 按名拒绝，
  未发布即停用，Docs 路由在 all 不受影响）。
* 守卫：check_boundaries（含新增“兄弟应用不得互相导入”规则，已用探针文件证实会报错）、check_brand、check_design、
  check_component_names、brand_assets --check 全部通过；`npm run typecheck`（全部工作区）通过；
  `npm test --workspaces` 通过（账户外壳的拒绝目录测试一度因存储导入能力层而失败，已把运行表 DDL 移入
  `suite_schema/runs.py` 修复）。
* 浏览器（内置浏览器，隔离实例：scratchpad `live/`，端口 33769/33770，文件邮件，夹具账户）：登录 → Forms 库 →
  新建 → 编辑器加字段/改名/必填/单选选项/显示条件（草稿自动保存，刷新后保留）→ 预览中条件显示生效 → 发布版本 1 →
  分享面板发出专属请求 → 请求详情复制链接 → 填写页：必填校验并聚焦、自动保存“草稿已保存 00:29”、刷新恢复草稿、
  切换选项后提交 → 提交详情中隐藏字段未入库 → 要求补充（逐项说明）→ 填写页显示说明并带入上次答案 → 第 2 次修订 →
  确认 → 修订历史。窄屏（646 px）布局。过程中修复：工作空间绑定使在途响应失效导致列表一直“加载中”（先绑定再渲染）、
  描述标签与输入框并排、必填勾选框布局、字段卡片与品牌链接缺少可访问名称、抽屉内按钮被共享样式拉成整行。
* 未做：英文/繁体与宽屏的浏览器走查、API 机器凭据调用（WP9）、Docs/Message 联动（WP7）。

## 2026-09-30 Codex 接续核查

- 主检出 main @ 01082ba；业务分支 d16a89b，Agent 后端、Markdown 渲染器及相关共享扩展未提交。三个应用子工作树仅有未提交的 schema/字段初稿，不能计为完成。
- 旧协调者在 Claude UI 显示 Weekly limit reached/context full；经核实停止后更新本专项锁持有者，未停止任何无关进程。
- GitHub run https://github.com/WeGreen-AI/Synwevo/actions/runs/36569205283（e4971c1）：verify failure、steps=[]，check-run 109408657447 注解为账单/支出限制；deploy skipped。
- workplace 公开 /healthz 返回 status=ok，仅为健康探测，不证明本专项上线或确定代码版本。

## 2026-09-30 WP2 未提交 checkpoint（business-apps @ d16a89b + 增量）

- Agent 后端审查子任务冻结时 75 passed（含 4 个 SSE 撤权/续传测试）；协调者再修复通用 `op_runs` 详情、列表与幂等重放的历史快照权限。新增 6 个测试覆盖成员撤权、资源删除、应用停用、字段可见性降级、个人会话派生内容与旧密钥脱敏。全部通过。
- 最后增量专项命令：显式 worktree PYTHONPATH + 主检出 .venv，`pytest apps/suite/tests tests/integration/test_suite_mount.py tests/integration/test_blob_references.py -q`，exit 0。仅 Starlette/SWIG 既存弃用警告。全量 pytest 正在执行，不能计为通过。
- `npm run typecheck && npm run build && npm test --workspaces --if-present` exit 0（全部外壳）。suite 前端 15 tests pass；五项仓库守卫与 git diff --check 通过。
- 真实浏览器、隔离 OIDC 账户 Alice：新建会话、长名称重命名、刷新保留、A/B 两空间范围、归档/恢复、英语/繁体切换、设置页新增个人偏好并停用记忆。手机 390×844 与桌面 1280×720 检查；键盘打开/关闭范围弹窗后焦点恢复，控制台 errors=[]。截图 [繁体手机](evidence/2026-09-30-agent/agent-mobile-hant.png)、[英文桌面](evidence/2026-09-30-agent/agent-desktop-en.png)。
- 重启本任务 portal（33769）后，深链接会话、名称、双空间范围和未配置状态从持久记录恢复。仅重启核实 cwd 与实例身份属于本任务的进程，保留 accounts 和其他任务。
- 未配置真实模型，发送正确禁用；未发出模型请求，不能算真实模型或工具整链路验收。生产未启用 Agent，也未提交/推送/部署。

## 2026-09-30 15:57 集成与审批恢复

- WP2 checkpoint 已本地提交 a472820，尚未推送或部署。前述专项与 UI 证据对应这个提交；完整模型链路仍未配置。
- a472820 + 未提交三应用：Tables/DataPrep/Approvals 接入统一路由，新增 blob 引用登记、Tables 记录深链接及 Agent 组合资源确认；共享调用在取得写锁后重新鉴权。专项 suite + suite_mount + blob_references 日志 /private/tmp/synwevo-integrated-targeted.log exit 0，套件前端 typecheck/build exit 0。
- 审批浏览器：Alice 创建并发布模板 apt_2aad1f80b504/v1，必填资料字段 f_f1f2b6cbe83c；创建请求 apr_b6f73c31dd1d，固定修订 1，唯一审批人 Bob。空必填提交未创建申请。发起人无审批决定按钮。仅重启已确认 cwd 的自有 portal 45264 → 48905，深链接刷新后原内容/修订/待审状态恢复，Bob 经真实 OIDC 登录后显示决定控件。尚未提交决定、未验证流程下游，不能计整个 B3 通过。
- 全量 unrestricted pytest session 94436 仍运行；已有一个 F，等待最终错误详情。受限沙箱的 PDF 分页用例失败，在允许本机排版进程的同一环境/用例下通过，未改断言。

## 2026-09-30 16:26 三应用真实操作与回归诊断（a472820 + 未提交增量）

- Approvals：apr_b6f73c31dd1d 在重启等待后由 Bob 提交批准与测试意见，时间线仅一项决定，刷新不重放。图：[等待](evidence/2026-09-30-approvals/pending-mobile-hant.png)、[已批准](evidence/2026-09-30-approvals/approved-mobile-hant.png)。这不替代完整 B3 流程下游证据。
- DataPrep：真实上传 business-input.csv（5行：前导零、空值、重复编号、bad金额）；方案dpr_c7542c530a55/v1，样本范围1–2/5；全量dpu_3af1d497b26c为partial，逐步5→5→4→3，文件第4行invalid_decimal。结果dset_dc62a1ff2250含0001/123.40、0002/0.10、0004/null；异常清单下载事件完成。图：[桌面结果](evidence/2026-09-30-dataprep/result-desktop-en.png)、[繁体手机](evidence/2026-09-30-dataprep/result-mobile-hant.png)。移动页宽390，无页面横向溢出，内部网格可键盘横移。
- Tables：真实UI创建tbl_83f4c65f3741，唯一必填文本fld_db245fcd5939与HKD金额fld_455be25e32f2。快捷键保存rec_edca4ce7d3ff，第一页面把123.40改为124.40/rev2，第二页面以rev1提交999.00被拒，载入最新保留124.40。重复0001被拒；改0002创建rec_58647e8223f5，归档rev2、恢复rev3，网格共2行。[冲突](evidence/2026-09-30-tables/concurrent-conflict-desktop.png)、[英文网格](evidence/2026-09-30-tables/grid-desktop-en.png)。确认框现复用统一DialogProvider，明确表名/记录，恢复经键盘确认。新浏览器页视口覆盖未实际生效，Tables手机证据未计通过。
- UI修复：DataPrep撤销编辑翻译避免误为撤销授权；审批按钮使用业务措辞；Tables金额历史显示精确金额/币种。Tables/DataPrep失败重试保留原幂等键，包括写入已提交但结果隐藏的4xx。前端typecheck/build与15项测试通过；Tables统一弹窗修改后typecheck/build再次通过。
- 完整旧回归 /private/tmp/synwevo-business-full-unrestricted.log 最终4失败。SDK目录来自主检出安装包（catalog差异），Push3项因系统代理导致RemoteProtocolError。显式worktree SDK路径与回环NO_PROXY后同5项专项通过（/private/tmp/synwevo-push-direct.log）。未改产品代理策略或测试断言。
- 同次日志暴露Docs解析重试线程在close之后提交已关闭执行器：现重试/关闭共用锁，关闭标记拒绝新重试，旧timer不能移除替代timer。apps/docs/tests/test_parse_shutdown.py三项通过。修正环境后的全量回归session88953仍运行，不能计成功；输出和JUnit前缀synwevo-business-full-isolated。
- Extract回收文件独立17项测试exit0（/private/tmp/synwevo-extract-recovery-tests.log）；Flows/适配器子任务已终止并交付，尚未在协调分支验收。Git/CI/生产状态未变化。

## 2026-09-30 16:38 代码检查点与全量回归

- 7bb3f95：Docs 停机重试竞态；03d4ff9：Tables/DataPrep/Approvals 与共享契约集成。均为普通本地提交，已正常推送 origin/feat/business-apps。草稿 [PR #1](https://github.com/WeGreen-AI/Synwevo/pull/1) 已创建；未合并/部署。
- 03d4ff9 的 Python 内容与完整回归运行时一致：/private/tmp/synwevo-business-full-isolated.log，JUnit /private/tmp/synwevo-business-full-isolated.xml：3053 collected，3051 passed，0 failures/errors，2 skipped，239 warnings，1080s。跳过原因分别为 CLAMD_TEST_ADDRESS 未配置与项目尚未选定 LICENSE；没有隐藏或删除失败测试。
- 最新五项仓库守卫 exit0（537 Python/291前端、11边界/8包、22Logo）；suite typecheck、15测试与build exit0。运行环境显式 worktree SDK/各包 PYTHONPATH，NO_PROXY 回环；未改变产品代理策略。
- Tables 搜索0001并保存视图 view_abe7515b7c2d，刷新深链接后仍显示唯一符合的记录/124.40 HKD。DataPrep 运行页以固定版本显示清理空白→类型转换→去重与实际数据集名称，[运行步骤](evidence/2026-09-30-dataprep/run-steps-hant.png)，控制台无错误。
- 主检出另一个任务推进到14b9811；未动其文件。读取主分支保护API返回403（当前计划不支持此接口），不能由此宣称没有门禁；本次仅推功能分支/草稿PR，没有改保护设置。
- Extract后续改动不包含在上述已通过提交中，需单独验证。已发现并修复其初稿把异步受理记为成功、共享运行回执不随任务更新的问题；新增队列→等待复核→确认/取消专项与真实PDF来源测试，结果待收取。

## 2026-09-30 16:56 HKT · Extract 检查点 352526f

- 实现：原 Docs DocumentIngest 由 server 注入；异步回执随真实排队/复核/确认状态更新；正常部署中断可在两次预算内恢复纯解析；人工结果不能伪造机器来源。
- 浏览器发现并修复 multipart 经 JSON 序列化造成422；共享 api 保留真实 FormData、CSRF、显式 workspace、幂等键，新增风险测试。发布失败保留同一草稿与修订，不重复创建模板。
- `/private/tmp/synwevo-extract-final.xml`：204 passed、0 skipped，56.34s；所有 suite 与 PDF/挂载/blob专项。`/private/tmp/synwevo-extract-final.log` 对应352526f后端。共享auth-client41项与suite前端15项通过；所有前端typecheck/build通过（`/private/tmp/synwevo-extract-all-front-build.log`）。五项边界/品牌/设计/组件/Logo检查通过；修复Extract私有!important，未降低门槛。
- 真实浏览器/隔离Alice：extpl_296ef6d1acb5/v1；PDF extask_ecee09ec2b6d；000127、123456789.01准确，备注missing。修正备注→r2且unverified；无ack确认被拒，ack后r3成功；JSON真实下载，刷新只有一修正一确认。
- 实际本机文字识别存在chi_sim/eng/osd；扫描件extask_651c541d466e准确提取同编号/金额，人工确认r2。OCR文本Issued误认lssued如实保留，未映射该字段，不把它报告为全部文字无误。
- 截图：`evidence/2026-09-30-extract/{source-narrow588-hant,confirmed-narrow588-hant,ocr-desktop-hant,ocr-mobile-hant}.png`。真实视口1280×800、390×844；手机scrollWidth=390，无页面横向溢出。浏览器console错误为空。
- Tables此前视口工具故障已恢复；390×844真实补验保存视图→键盘Return开记录→历史金额124.40；`evidence/2026-09-30-tables/{grid-mobile390-hant,record-mobile390-hant}.png`。原名grid-mobile-hant.png的/tmp旧图不是手机证据，未采纳。
- 352526f只是本地验证检查点，尚无CI或部署成功声明。PR#1此前DIRTY且无run，现正常合并远端origin/main @4b74796，不引入主检出未推送提交。

## 2026-09-30 WP7 当前集成检查点（8e8fcec + 未提交增量）

- 8e8fcec 正常合并 origin/main@4b74796 并推送；210项合并专项通过（/private/tmp/synwevo-main-merge.xml）。所有前端 typecheck/build/test 与五项守卫通过。主检出同伴代码未修改。
- 当前提交 [Actions 36693142151](https://github.com/WeGreen-AI/Synwevo/actions/runs/36693142151)：verify job109814773242 steps=[]；注解为 recent account payments have failed or your spending limit needs to be increased，deploy skipped。PR UNSTABLE；没有 CI 通过或部署证据。
- Flows/适配器已接入应用工厂、真实 Docs worker 生命周期和 portal 健康探测；Extract 解析器注入保留。/private/tmp/synwevo-wp7-integrated.xml：270 passed、0 skipped、6 warnings、90.50s；含真实 Forms→Approvals→Forms 等待/重启/重复事件恢复及17项Docs/Message适配器测试。尚不能替代七应用B1。
- 后续库存守卫发现 flow_runs.input_digest 未分类，已按实际含义登记为流程输入指纹（不承载blob），等待专项复验；不删除检查或降低断言。

### 2026-09-30 WP7跨应用文件输入与隔离（8e8fcec上未提交增量）

- `synwevo-wp7-files.xml`：282 tests，0 failures/errors/skips，97.167s。实际Forms附件→Extract真实CSV解析/来源→人工确认→DataPrep，金额123456789.01和编号000127保持；各合法请求独立，同键重放不复制新文件。
- 故障注入后字节仍存在于Store，quarantine事实阻止Forms/Extract下载、提取历史、衍生数据集和Extract排队worker；没有将测试隔离判定作为实际扫描证明。
- suite前端类型/15项模型测试/构建通过；boundaries/design/component检查通过。Logo初次用了无Pillow的系统Python而失败；以项目.venv复跑22衍生文件一致。brand全部1177处已归类，diff检查通过。
- 这是本机隔离验证，不等于完整B1、真实模型或上线验证。DataPrep取消的来源依赖问题单列CR-007，正在修复。

### 2026-09-30 WP7提交前最终门槛

- `/private/tmp/synwevo-wp7-final.xml`：285 tests，0 failures/errors/skips，162.215s；后端代码为8e8fcec后的当前WP7增量。20项DataPrep隔离/来源取消专项亦全过；新增测试初次导入路径和数据库列名错误已修正，未削弱业务断言。
- Suite前端35项测试、类型检查、生产构建通过；边界588 Python/303前端/11模块/8包、设计、组件检查通过；品牌与Logo此前同批通过。
- 真浏览器：flow_54d623bdc01b/v1，flowrun_a53e36718407，两步分别读取Acme与只读Beta；实际Forms业务结果、4次预算消耗、唯一运行与刷新恢复核实。手机390×844 scrollWidth=390、长标题/键盘确认无阻碍，console error/warn为空。截图在evidence/2026-09-30-automations。desktop-run为发现日期标签问题时的截图，后续mobile-deeplink/disabled-trigger已确认修复。
- flowtrigger_608f19d50fc4：Asia/Hong_Kong09:00工作日、跳过超出300秒宽限、最多1次、明确至撤销；创建后立即UI停用，刷新保持停用，未产生触发记录。此验证不是计划时间到达后的真实调度验收（后端调度/重复事件/时区故障专项另有测试）。日期控件工具填入未保留，空期限被正确拒绝，指定日期UI路径未算完成。
- 当前模型核验：项目内新发现已忽略产品凭据；权限0600，未输出/复制密钥。官方GET /models及/user/balance均200，API声明deepseek-flash(DeepSeek-V4.1-Flash)和deepseek-v4-pro可用，is_available=true；未记录余额金额。没有真实推理/计费/有效路由验证。旧缺密钥阻塞改为网关、价表和实际推理待集成。
- E4仍受Actions账户限制；本地通过不等于CI/部署通过。

### 2026-09-30 WP7已推送与DeepSeek接入开始

- 普通提交d453e3da61933bccf916f02770518363adfd7dc5已推送feat/business-apps；68文件范围审查、敏感凭据对照检查及diff检查通过。此前285项最终回归、35项前端与真实双空间个人自动化证据归于该提交。
- [Actions 36703155098](https://github.com/WeGreen-AI/Synwevo/actions/runs/36703155098)：verify109847073304未启动、steps=[]，注解仍为账户payments失败或spending limit；deploy skipped。没有CI/上线通过声明，未绕过门禁。
- d453e3d后未提交DeepSeek三文件及httpx直接依赖：协调者重验148项协议/Agent测试通过，0失败/跳过，JUnit /private/tmp/synwevo-deepseek-integrated.xml。不是实际推理或收费验证。
- Admin专项已暂停，d4dd1ba工作树干净；原报告退出Timer竞态需检查是否被业务分支7bb3f95覆盖，其他完整门槛仍待集成后重验。

### 2026-09-30 Admin/API统一集成与计费网关

- 本地27823e7保存DeepSeek适配器；e9b9f2c正常合并main@2dd03c7，43b221c正常合并codex/admin-platform@d4dd1ba。四处文本冲突保留suite+SDK安装、等价回环隔离注释及两套错误词库；未改主检出未提交文件。
- 合并专项JUnit /private/tmp/synwevo-unified-merge.xml：632项，614通过，18夹具错误，0失败/跳过，261.362s；根因旧夹具直接设置platform_role，新权限模型要求实际grant。改用PlatformAccess测试授权，36项受影响文件/适配器/Agent回归全部通过（synwevo-unified-fixture-fix.xml，24.765s），原拒绝断言保留。Admin原退出Timer竞态修复7bb3f95仍存在，专项通过。
- 所有前端typecheck/test/build完成，日志synwevo-unified-front.log；5项守卫通过（631Python/320前端，1185品牌分类，22Logo）。gh pr edit遇弃用Projects字段，改官方PR REST更新同一描述成功。一次隔离测试权限自动审查超时，按返回指示仅重试一次成功，未绕过权限。
- 账本由协调者完整走读并接入Store：无余额/价格种子；test/live、整数复式、原子预留、一次dispatch、未知期限释放、迟到成本不补扣。首次库存检查发现2个新指纹列缺分类，已说明用途补全；原守卫通过。
- DeepSeek→持久网关→Ledger→Agent真实Forms工具循环：synwevo-agent-billing-final.xml 100项通过（0失败/跳过），包含47账本测试、11网关测试、39Agent测试及3库存检查。新测试初次SSE夹具缺object字段与Store snapshot已解码处理均已修正；没有削弱断言。协议流、支付授权事实均明确隔离替身，不是实网调用。
- 实际Agent/Forms用例：创建运行，预留后发送；请求创建表单进入waiting_confirmation，确认后生成一份表单；第二次模型收到原生私有上下文和工具结果；两笔独立用量/结算，恢复不重放，公开会话无隐藏reasoning。换提供方后拒绝重用旧私有历史。未知usage不置零，数据库结算故障从已存usage恢复不再发模型请求。
- 当前增量尚待提交和完整联合门槛；不是CI通过/上线/实网计费验收。生产Agent工厂没有自动开启收费路线。

## 2026-09-30 价格治理与计费故障修复（41069bd 后增量，临时验证树）

- 为保持完整基线测试版本一致，在 `/private/tmp/synwevo-price-validation` detached@41069bd 验证新改动；尚未整合进协调分支。未改主检出的应用图标专项文件。
- 价格治理30项后端测试通过（`/private/tmp/synwevo-model-prices-final.xml`，20.572s）；含真实HTTP草稿/精确试算/签名发布、不可变历史、同键重试、两实例并发、撤权/会话/近期认证变化与审计回滚。仅合成FIX_UNIT测试价格，不开启正式收费。
- CR-008/009/010：在销售结算前校验模型拒绝/截断/重复工具ID；dispatch事务内阻止同运行未决并发；付费尝试开始前持久pending。6项新增故障测试覆盖实际Agent循环与隔离模型响应。独立审阅复查确认原路径已覆盖。
- 联合158项全部通过，0失败/错误/跳过，70.727s（`/private/tmp/synwevo-price-final-combined.xml`）：价格/配置/Agent/流式/账本/blob引用。价格接口随后补充类型契约，单独追加验证中。
- Accounts前端类型检查、23项测试和build通过（`/private/tmp/synwevo-prices-front-final.log`）；最初遗漏拒绝码模块分类已补齐，未放宽测试。五项仓库守卫及3项blob检查通过。
- 自有隔离Accounts实例33772健康200/instance=test；实际页面导航被内置浏览器返回net::ERR_BLOCKED_BY_CLIENT，原地址复核仍被拦截，未更换地址/控制方式规避，E2尚未通过。测试数据在`/private/tmp/synwevo-price-browser-data`。
- 当前remote仍d453e3d；再次查询Actions36703155098：verify failure/steps空，deploy skipped。未推送新代码、未部署；41069bd完整基线回归尚在执行。

- 价格严格类型追加后36项通过（`/private/tmp/synwevo-price-contract.xml`，24.499s），API OpenAPI输入/输出真实匹配；无值回显，布尔/字符串不被隐式转换成用量。测试新增代码最初误放入并发用例尾部，导致NameError，已恢复原并发断言位置并重新通过，不删减断言。
- 当前增量本地提交：0371c20（三项计费修复）、77d4b2a（价格治理17文件）。新类型之后Accounts前端23项再验通过，五项守卫通过；Logo检查最初误用无Pillow的系统python，使用项目已安装venv运行后22文件匹配。尚未整合进feat/business-apps、未推送。

## 2026-09-30 20:22 HKT · f6fb865 计费工作包整合

- 41069bd完整基线XML：3767 collected、3765 passed、2 skipped、0失败/错误，4065.211s；跳过为未配置CLAMD_TEST_ADDRESS与未选定LICENSE，未改测试规避。session54759正常exit0。
- f6fb865整合0371c20计费修复、77d4b2a模型价格治理、f6fb865个人钱包。通过fast-forward保持原提交，不修改主检出其他任务。旧8文件草稿存于stash513202cb1e188d613472d1506d755f238d9d14ed。
- 钱包/账户安全/隐私44项通过（`/private/tmp/synwevo-wallet-http.xml`，25.518s），包括新增实际默认factory接线用例；账本/Agent/价格100项通过（`/private/tmp/synwevo-wallet-ledger-regression.xml`，36.190s）。钱包子任务25项与并发快照检查由协调者实读复验。
- Accounts类型检查、25项前端测试与构建通过；准确金额显示覆盖大于2^53、负微额与18位精度，简繁英词库及拒绝码完整。五项守卫通过；最后UI环境提示文字后再次typecheck/build通过。
- 隔离SQLite投影：10,001条合成流水、1单位、单并发30次，median2.289ms/P952.848ms/max2.904ms；只验证本机读侧，非HTTP/支付/模型负载或生产SLA。`/private/tmp/synwevo-wallet-bench.json`。
- 价格与钱包页面仍未完成真实浏览器验收；客户端拦截隔离地址。正式支付、生产价表、真实模型收费链路与上线验收均未完成。测试均不代表真实充值；正常推送候选分支不代表发布。

### 2026-09-30 — 0609ff5推送与新CI核验

- 普通push：d453e3d → 0609ff57e31dea79b1805b6166d63cc795f54ad3，开发分支feat/business-apps；草稿PR #1范围/证据已更新。
- 精确HEAD的[Actions 36714880199](https://github.com/WeGreen-AI/Synwevo/actions/runs/36714880199)：verify109885245745 failure/steps=[]；annotation明确recent account payments failed or spending limit needs increase；deploy109885264587 skipped。无CI通过、部署或上线声明。
- 报价进行中：21项专项通过（/private/tmp/synwevo-run-quotes.xml）；曾发现测试fixture Store实例与Agent事务分离、测试误用Capabilities.read、真实result_guard误把quote当turn，分别修正实例、调用inspect、报价权限回放分支；未删有效断言。

## 2026-10-01 · 单次预算报价与有限对账验收（0609ff5 后增量）

- 真实Store/Agent/Ledger及HTTP链路：`/private/tmp/synwevo-quotes-recovery-final.xml` 235 passed，0失败/错误/跳过，43.087s；报价22、恢复45、生命周期3与关联回归。协议和资金使用明确隔离夹具，不是实网推理/真实付款。
- 恢复器有界读取、逐项隔离、到期释放、可信计量结算与终态usage投影；事件和投影同事务。两worker与重复执行不重复扣费或事件；不会重放模型/业务工具。
- 最终Suite类型、41项前端、build通过（BudgetDialog-DG3jNFmf.js）；五项守卫通过：644Python/330前端、1185品牌分类、22Logo。审查发现新增binding_digest未在blob库存分类，按实际非blob用途登记；原3项库存测试通过（synwevo-quotes-blob-final.xml）。
- 浏览器使用已有portal.localhost:33769隔离数据；临时测试装配无余额且模型网络一律拒绝。conv_fa7f3a332c4b保持唯一turn_07f86a79cda1/failed/billing_insufficient_funds；真实Store读侧核对browser-budget-fixture的attempt=0、journal=0。刷新和报价/关闭不新建运行。
- Enter获取报价后焦点曾丢到页面，修复为总预算区域；实际复验Tab仍在弹窗、Escape回到重新生成按钮。预算2.969在2位精度下拒绝；3.00固定报价成功。三语言实际切换；英文单token复数标签已修复。390×844页面scrollWidth=390，dialog358×759.59；桌面1280×900、长标题、可滚动正文/固定按钮已检查，console error/warn为空。
- 截图：[手机报价](evidence/2026-10-01-agent-budget/quote-mobile-hant.png)、[英文桌面](evidence/2026-10-01-agent-budget/budget-desktop-en.png)、[简体精度拒绝](evidence/2026-10-01-agent-budget/precision-validation-hans.png)、[余额不足](evidence/2026-10-01-agent-budget/balance-refusal-desktop-hans.png)。手机图早于最后单token措辞修正，布局/焦点代码相同。视口已reset；未宣称真实reduced-motion或完整WCAG验收。
- 验收后关闭浏览器事件连接，TERM退出PID74817/41002，恢复普通portal PID77949/session44945，healthz200/instance=test；未启用付费模型。33770/33772旧进程已停止，不沿用历史健康证据。
- 235项是当前后端增量的关联验证，不是当前提交全仓重跑；全仓基线3765/2仍只绑定41069bd。提供方/成本准入、周期预算、持续授权、支付/退款/商户与真实模型验收仍未完成；正式收费保持关闭。

### 2026-10-01 · 7190408推送与成本准入开始

- 普通提交7190408428b5e1b86c4b6295888452e42bab6832已推送，28文件；预算/恢复证据归于此提交。草稿PR #1已更新；未合并、未部署。
- [Actions36800670629](https://github.com/WeGreen-AI/Synwevo/actions/runs/36800670629)精确headSha=7190408；verify110174044102的依赖安装、Verify and build成功，Verify the deployment runtime仍运行。旧账户额度失败不再代表当前CI。
- 提交前初次文本扫描试图UTF-8解码PNG失败；推送前改为bytes扫描实际git show，通过高置信秘密模式检查，四张图片已视觉审查。没有把失败扫描写成通过。
- 新增成本准入包尚未验证，正式DeepSeek/收费仍未启用。

### 2026-10-01 09:46 HKT · 平台成本准入与未知用量敞口（7190408后增量）

- Store新增不可变admission与pending投影；Ledger同一FULL事务提交成本准入与一次发送边界，按稳定提供方账户跨namespace累计固定UTC日/周/月、未知总额/数量与在途连接。实际成本使用精确分数，不因平台故障、迟到或超过估值被截断。用户预留最终释放不清除未知平台敞口。
- `/private/tmp/synwevo-cost-final.xml`：289 passed，0失败/错误/跳过，38.395s，当前完整成本增量+Ledger/Agent/报价/恢复/生命周期/blob关联回归。独立8个Store争抢最后预算只有1个准入；投影/发送/结算故障均回滚；重复打开和一次性回填保留事实。没有真实支付或模型流量。
- 修复CR-013：10,000条历史已结算记录下捕获实际SQL并EXPLAIN，当前区间与历史pending分别使用scope索引，没有底层全历史扫描；最多10001候选溢出探测，最多10000参与累加，超限拒绝，不默默截断。
- 修复CR-014：明确未发的成本拒绝显示已知0；终态恢复同样识别无dispatch时间戳的已释放attempt。一次真实Agent工具调用后下一步被成本拒绝，保留单次工具、已计量13输入/7输出与一次销售；真正发送后未知仍pending。
- [本机成本快照测量](evidence/2026-10-01-model-cost/admission-benchmark.json)：macOS27/arm64、Python3.14.5、10000条合成pending、单并发30次，median24.863ms/P9530.647ms/max34.036ms；不代表HTTP、支付、真实模型吞吐或生产SLA。
- Suite typecheck/41前端/build通过；五项守卫通过（648Python/330前端、1185品牌分类、22Logo）；diff检查通过。只新增错误文案，沿用已验收的预算/错误布局；本包未新做浏览器流程，不沿用它冒充新现场。
- 正式付费模型仍未启用；提供方账户/路由治理、付款周期预算、RPM/TPM/公平排队、成本调查Admin、支付与真实收费验收尚未完成。7190408的CI部署容器检查仍在进行，尚无本包CI/上线声明。

- 2026-10-01 09:50 HKT：普通提交`1bc6c427aa9e8ca2c4b37490951358cf7355e437`已正常推送feat/business-apps，18文件。staged diff/高置信秘密模式检查通过；PR仍draft且mergeable_state=clean。精确SHA的[Actions36802776113](https://github.com/WeGreen-AI/Synwevo/actions/runs/36802776113)为pending，按既有concurrency等待7190408运行完成；没有取消门禁或重复手工部署。

### 2026-10-01 · 成本策略治理当前增量（尚未提交）

- Store不可变策略版本、实际CostAdmission消费者、四个类型化管理API及三语言编辑/预览/发布/历史已实现。平台view/manage分别鉴权；签名绑定环境/当前版本/操作者/源站/固定账户与精确参数；新发布/成功审计/幂等结果同事务。
- `/private/tmp/synwevo-cost-governance-final.xml`：257 passed，0失败/错误/跳过，78.161s；其中HTTP72、核心策略14，另含成本准入125、Agent成本10、价格36。停用即时拒绝新发送，已发生用量仍结算；重复/竞争/撤权/审计回滚真实验证。隔离夹具，不是外网模型或付款。
- Accounts typecheck、31项前端+19项组件检查、build通过；五项守卫通过（653Python/334前端、1185品牌分类、22Logo）。前端浮点expires_at契约不匹配已修正；测试后缀改为默认门槛实际加载的mjs。新接口拒绝词句在默认语言检查中登记并通过，没有修改断言标准。
- 本地Accounts健康200；内置浏览器对已配置prices.localhost:33772返回ERR_BLOCKED_BY_CLIENT，当前UI视觉/键盘/窄屏未验收。未切换工具或弱化安全来绕过。正式收费仍关闭。
- [CI7190408](https://github.com/WeGreen-AI/Synwevo/actions/runs/36800670629)在45min时限取消，构建成功，容器测试到49%并已有1F；日志`/private/tmp/synwevo-ci-7190408.log`。没有最终traceback/全套结果，不能归为只超时无业务失败。1bc6c42新CI运行中。

### CI首个失败：部署Python版本下的契约漂移

- 首个F定位到第92项OpenAPI文件同步。使用既有`de70b4cb2882` Python3.12验证镜像、只读当前源码挂载（不是本提交新建生产产物），断网/3GiB/2CPU，只跑该失败定位，复现413与416默认短语跨版本差异。
- `v1_uploads.py`固定既有公开协议文案；没有改文档快照、业务限制或测试断言。最终`/private/tmp/synwevo-ci-openapi-fixed.log`：2 passed/1.38s；`/private/tmp/synwevo-openapi-py314.xml`：2 passed/1.22s。先前本机上传/OpenAPI关联17项通过5.98s；第二个字段后以两项同步/结构用例重新绑定。
- 验证镜像CMD增加--maxfail=1/--durations=20/-ra，以在首个失败时打印traceback而非等待外层超时，成功仍执行全部套件。45分钟容量诊断继续；不是CI通过或部署声明。
- 成本策略`dee89fb`已本地提交，22文件/暂存秘密模式与diff检查通过；尚未推送。自有Accounts6763已停止，隔离数据保留。

### 2026-10-01T14:30:06+00:00 · CI修复与协作内存生命周期（dee89fb后的未提交增量）

- [1bc6c42 CI](https://github.com/WeGreen-AI/Synwevo/actions/runs/36802776113)：verify110184975517于02:51:37Z取消，已实际执行构建与容器测试；deploy110195960046 skipped。没有发布。
- `/private/tmp/synwevo-collab-memory-red-final.xml`：原产品四项真实Room生命周期均失败（6.568s），每项6个隔离应用，保留API/Store/Hub/Room/Doc。草稿内容、关闭、SQLite故障与重开断言先通过。只记录合成材料，不含用户数据。
- 当前协作回归 `/private/tmp/synwevo-collab-lifecycle-regression.xml`：210 passed，0失败/错误/跳过，71.822s；撤权、并发、版本、保存、恢复、表格、项目presence保持通过。初次“关闭时退订”实验跨线程错误已改为操作内注册/退订，不屏蔽异常。
- `/private/tmp/synwevo-ci-repair-validation.xml`：33 passed，0失败/错误/跳过，17.879s，包含4项新内存回归、文档CRDT转换、OpenAPI/上传及真实工厂生命周期。初次命令误写不存在的test_api_uploads.py以exit4结束，按实际test_api_contract_uploads.py重选；没有删减测试。
- 五项守卫通过（654Python/334前端、1185品牌分类、22Logo），diff格式检查通过。当前未改前端，无新浏览器UI声明。
- Python3.12既有隔离镜像关联测试进行中；新完整CI尚未开始。45min上限及全部测试范围保持，正式模型/收费没有启用。

- Python3.12最终隔离关联50项全部通过（exit0，`/private/tmp/synwevo-ci-repair-py312.log`），覆盖完整text协作、新内存回归与OpenAPI。未升级依赖；不是生产镜像构建/完整CI通过声明。Hilbert已确认停止，测试文件所有权回到协调者。

- 提交并正常推送`dcca816d3e843278362e5ee2e71396efd1f58ff2`（9文件），含CR-016–018修复/独立回归/检查点；成本策略dee89fb一并推送。暂存高置信秘密模式与diff检查通过。既有草稿[PR #1](https://github.com/WeGreen-AI/Synwevo/pull/1)已更新；精确SHA的[CI36877286851](https://github.com/WeGreen-AI/Synwevo/actions/runs/36877286851)当前构建检查通过、运行时回归进行中。没有重复部署。

### 2026-10-02 · 中断恢复与 dcca816 失败定位

- 精确HEAD的Actions36877286851：构建通过，运行时验证2289 passed/1 failed/3 skipped，1770.76s，未达到45分钟超时；deploy skipped。失败为管理路由与权限矩阵不一致，新增价格7/成本4端点缺席；日志 `/private/tmp/synwevo-ci-dcca816-job.log`。gh --log-failed为空，改读实际job110419843983日志定位，未盲目重跑。
- 当前提供方配置/凭据/跨空间修复联合197项通过（`/private/tmp/synwevo-provider-integrated-core.xml`，隔离协议/资金，无真实模型或付款）；未提交、非CI或发布证据。

### 2026-10-02 · 治理配置、工厂与权限闭环（当前未提交增量）

- 管理/价格/成本/权限矩阵/blob引用联合261项通过，0失败/错误/跳过，114.212s：`/private/tmp/synwevo-provider-admin-final.xml`。Identity默认工厂真实挂4端点各一次，不借测试手工挂路由。
- 全部Agent、流、报价、计量/恢复、DeepSeek适配与提供方政策304项通过，0失败/错误/跳过，54.514s：`/private/tmp/synwevo-agent-governance-full.xml`。来源/目标归属与凭据binding同时覆盖；旧组织授权不扩张。
- 现有Python3.12镜像只读、禁网、3GiB/2CPU：权限矩阵+提供方HTTP+核心150项通过，exit0（`/private/tmp/synwevo-provider-matrix-py312.log`）；该诊断镜像不是当前生产产物。
- 实际Suite工厂11项通过，4.057s；真实表单读取、两次模型步骤/独立结算与恢复不重复；在预留后分别旋转密钥、停用、取消、撤账户/应用、归档、关闭成本均阻止发送并释放预留。模型SSE/支付凭据为隔离夹具。最初测试误用操作名/余额方法/账本表以及重复上游请求ID，已按实际契约修正；未改业务断言标准。
- 新增路线日期和模型标识边界后核心/工厂33项复验通过：`/private/tmp/synwevo-provider-contract-final.xml`。五项守卫通过（664Python/338前端、1185品牌分类、22Logo）；后续UI政策落盘后仍需最终守卫。
- Accounts真实管理入口`/admin?tab=model-provider`；简繁英56前端测试+19组件检查、typecheck/build通过。Suite政策页面仍在最终收尾。
- 自有Accounts33772 healthz200/instance=test；原prices.localhost被内置浏览器ERR_BLOCKED_BY_CLIENT再次拦截。没有更换源站或工具绕过；随后停止自有PID81168，保留隔离数据。当前UI视觉/键盘/窄屏验收未通过。
- 没有启用任何正式路线、价格或真实收费；dcca816 CI仍failed/deploy skipped。本包尚未提交/推送，完整新CI尚未执行。

- 最后单条政策快照/日期及模型标识边界后76项通过，20.918s（`/private/tmp/synwevo-provider-final-diff.xml`）；Suite46前端/typecheck/build通过，Settings-m-JyxStT.js；Accounts56前端复验通过。最终边界664Python/339前端，设计/组件与diff通过。三个子任务全部移交关闭。

- 2026-10-02T10:50:50.042308+00:00：提供方包正常提交并推送`bff35a8ac66e998e49bfa2d8bef1ab6d92bfe103`，49文件；暂存diff与高置信秘密模式检查通过。精确SHA的[Actions36997527305](https://github.com/WeGreen-AI/Synwevo/actions/runs/36997527305)运行中；草稿PR #1尚未合并/部署。


### 2026-10-02 · 个人日/周/月支出上限（bff35a8后未提交增量）

- 最终本机关联回归 `/private/tmp/synwevo-payer-integrated-final.xml`：544 passed、0失败/错误/跳过，87.050s；PayerBudgets/旧账务迁移/容量/原子故障、Ledger、成本/提供方、blob与全部Agent/DeepSeek关联。一次Starlette对AnyIO旧别名弃用警告，不是业务错误。早期526项及边界28项为过程证据，不叠加成更多独立测试。
- 独立审查发现跨UTC午夜检查与call写入时间不一致，统一固定timestamp；日/周/月三例验证。读预算操作加unattended=False，flow/个人自动化/模型不能读取或修改此个人配置。网关只保留payer_*原因，原unknown/fence契约不变。
- 另15项独立测试已审阅：有事实旧库迁移/两次reopen不改变账本；两个真实funded fixture单位独立限额；实际10000候选全部算入，10001拒绝；缺settlement无假零；event/audit实际INSERT后故障整笔回滚，独立Store看不到未提交版本，无revision缺口。
- Suite typecheck、61前端测试、build通过（Settings-C8AZOH7d.js），五守卫通过：670Python/343前端、1185品牌分类、22Logo；diff格式通过。仅把技术说明收进原生可键盘展开详情，保留实际支出/预留/禁用影响和具体确认。
- 浏览器正常OIDC登录已有隔离Alice，经portal.localhost:33769/agent/settings访问真实工厂和共享操作。1.001对2位精度拒绝并聚焦Daily；1.00/5.00/20.00 TEST_ONLY确认保存revision1。刷新/重新载入和历史核对仍只有1个版本。390×844繁体手机以键盘Enter核对停用+日限额0，返回编辑焦点恢复checkbox，确认保存revision2；重新载入保持2及disabled。三语言实际切换，周期时间可Enter展开；document/client宽390，无水平溢出，console error/warn为空。桌面1280×720和繁体手机截图已在本任务工具记录视觉检查；当前浏览器不支持content.export，未声称本包PNG已存仓库，也未声称真实reduced-motion或完整WCAG审计。
- 当前无真实模型/付款调用；测试预算只是限制，不生成资金或持续授权。自有服务仅使用既有隔离测试库、start_jobs=False；未改正式部署环境。
- CI容量诊断：Plato在Python3.12.14/2CPU/3GiB禁网容器抽样63项，基线→每10项GC→每10项GC→基线，共252通过、0源文件改动。平均总耗时11.708s→11.314s（3.4%），峰值258.8→285.6MiB；stop_all/cache微小，不支持以GC批处理解决45min容量。原清理保持。证据 `/private/tmp/colima/synwevo-capacity-profile-1wm78hiz/summary.json`及同目录节点/插件/runner；小样本ARM64，不外推完整CI。

- 最终Python3.12.14诊断镜像专项54通过、2弃用警告、12.75s（Nietzsche工具结果）；只读当前apps/packages/services/infra/tests/scripts/conftest/pyproject、禁网/3GiB/2CPU、tmpfs无真实数据。随后UI合法旧结算总额超过int64的显示校验已修，新增精确显示回归；最终62前端/typecheck/build通过（Settings-BoK2sgf4.js），不是重新增加后端运行计数。
- [bff35a8 CI36997527305](https://github.com/WeGreen-AI/Synwevo/actions/runs/36997527305)最终：2922 passed/1 failed/3 skipped，2377.14s；verify job44m57，失败SDK源码来源断言，非超时。实际SDK路径/usr/local/lib/python3.12/site-packages/synwevo_docs_sdk；日志/private/tmp/synwevo-ci-bff35a8-job.log。原3skip是Node另步执行、未配置clamd和未选LICENSE；未新增skip。deploy skipped，待修镜像与容量后新SHA验证。


### 2026-10-02 · SDK来源与完整CI容量修复（d3cba3c后增量）

- 个人预算包已本地提交d3cba3c212e20e528a9c933323df9b4008672de4，24文件；暂存diff与高置信秘密模式检查通过。为避免重复执行已知失败CI，待本修复一起正常推送。
- CR-027：验证Dockerfile将SDK常规安装到site-packages，与当前检出来源断言冲突。验证镜像改为和其他业务包一致的editable安装；未改断言/SDK功能/生产镜像，宿主机SDK检查继续测试常规安装。
- 使用实际infra/production/Dockerfile.verify、既有诊断基底构建synwevo:payer-sdk-verify-d3cba3c（image config b26baa3dcfde697c32014935fc3f8b6cb17392f925f96e150490a893a9c902c3）；当前源只读挂载、无网/3GiB/2CPU/无真实数据。失败的源码来源检查所在DataPrep/Extract文件17项通过；SDK按自己的pytest根配置独立运行27 passed、14subtests、11.14s，exit0。日志/private/tmp/synwevo-sdk-source-fixed.log与构建log。初次诊断基底无Suite安装导致collection error，明确加载当前业务包；随后同次混合SDK根配置导致test_http导入失败，按其已有独立配置分开执行。未跳过或改断言；SDK不经PYTHONPATH加载，仍验证新editable安装。
- 8项部署接收器unittest通过；workflow YAML可解析，verify→deploy/production环境/容器资源断言保留。主分支保护API返回403（账户套餐限制）；没有更改保护、审批、deploy条件或触发分支。
- 容量独立只读收集：bff35a8共4214项，失败为第2925项，余1289；d3cba3c当前4257项。按2925项/2377.14s线性估算，当前完整测试约57m40+前置5m14=62m54；75min提供约12min余量，非真实完整运行保证。证据/private/tmp/colima/synwevo-runtime-groups-4w9czjfy/capacity-comparison.json。
- 只将verify timeout45→75；全部testpaths、断言、清理、skip策略、Node单独必跑、原镜像及资源边界保持。发现Docs生成testdata而integration直接读，以及带时间ZIP参数ID不稳定；本次不引入未验证分组/产物供应链。后续完整新SHA CI仍须实际通过；未声称本地诊断镜像就是生产产物。

- 普通提交并推送6052c13979dc315a784b94a8673ad4a1d53d4e84（含预算d3cba3c）；草稿PR #1更新并附任务。精确SHA [Actions37002797970](https://github.com/WeGreen-AI/Synwevo/actions/runs/37002797970)运行中；未合并/部署。未重复触发手工发布。

### 2026-10-03 · 持续模型付款同意（feat/business-apps本地21f2cd5，未推送）

- 协调锁：Codex 01a0f104 goal paused、turn_aborted(by user) 2026-10-02T15:05Z、服务15:10Z退出且13小时无事件；df1f0b15于04:09Z接管并每轮更新heartbeat。
- [6052c13 Actions37002797970](https://github.com/WeGreen-AI/Synwevo/actions/runs/37002797970)：conclusion success；verify 74m36（预算75），4253 passed/5 skipped/4092s；deploy按分支跳过。
- 后端：新增test_agent_model_consents 23项（期限/次数/范围/int64总额/basis退役/冲突/唯一与上限/跨人跨会话/当前状态5种/删除/耗尽与撤销/期限裁剪/不可变/并发恰一/调用方5种/HTTP CSRF与404/审计仅ID）；关联61项通过。全Suite `apps/suite/tests` 568 passed/0失败/0错误/0跳过，283.358s（scratchpad/suite-consent-full.xml）。
- 测试发现并修复：ops.py未在模块级导入BillingError，同意的全部拒绝变成500；未找到改为404；总额溢出改专用码consent_total_too_large；列表同秒排序改rowid；撤销重复不再重复发事件；system与api调用不能创建/使用同意（仅UI）；每次运行期限同时受同意到期与route有效期裁剪。
- 前端：agent-consent 9项新测（总额/期限/范围/响应校验/状态/未知结果锁定与原key核实/拒绝解锁/三语言/渲染）；Suite前端71/71、typecheck、build通过；五守卫通过（674 Python/347前端、品牌、设计、组件名、22 Logo）。
- 浏览器（隔离accounts33770+portal33769 fixture路由，网络阻断，无资金）：OIDC登录；明确范围后报价→勾选持续同意→空次数与未选期限分别明确拒绝→3次直到撤销→核对5.00×3=15.00 TEST_ONLY→保存；过去到期拒绝→2次至10月10日12:00→保存→“按此同意发送原消息”→草稿清空、剩余1/2；面板撤销复核→已撤销；全局发现范围仅提示须明确范围、单次报价保留；勾“下一条改用单次报价”打开无同意选项的报价框；英文/繁体完整翻译；390px无横向溢出；键盘Tab顺序、Enter打开条款、Esc关闭且焦点回到触发按钮。
- 浏览器发现并修复：保存同意后页面刷新列表导致对话框撤回同意步骤并恢复已退役报价的单次确认按钮；改为对话框打开时固定同意选项，复验通过。非active同意不再显示“改用单次报价”。
- 数据核对（隔离库只读）：两份同意（一份已撤销）、各绑定1次、3条事件仅含consent_id/conversation_id；billing_attempts与billing_calls均为0。fixture凭据未标明deepseek提供方，网关在预留前以billing_model_configuration_changed拒绝，属于launcher夹具，不是产品回退。
- 未运行：Python3.12容器复验、完整三套件CI（因容量余量不足暂不推送）。

### 2026-10-03 · CI固定成本根因、限流修复与WP9合并（cf1bb32已推送）

- 根因测量（后台agent，scratchpad/ci-profile）：HEAD全量4383项/3016s中，每测gc.collect 685s、新库建表（约70次ALTER×700对象）670s、生产Argon2 600s、FastAPI建路由309s。
- 00c4f54（仅测试基础设施，无断言改动）：收集后gc.freeze、会话级已迁移模板库（meta只留schema_version，首开步骤照常执行；3项自建旧schema测试标fresh_store_schema）、测试专用Argon2参数（参数/重哈希/计时3项保留生产hasher）。identity A/B：571.5→455.5s，峰值250→191MB；全量4385项/1046s，0失败，2环境跳过，峰值657MB。
- 6533c2a：更快测试暴露GCRA以时间保存债务——清除3/min覆盖后旧债务按1200/min算满一分钟、收紧限额又给新突发。改为记录计费间隔、按请求数换算，收紧最多满桶且拒绝时也记录换算。新增单元与HTTP回归各1项，相关62项通过。
- [6533c2a Actions37103506897](https://github.com/WeGreen-AI/Synwevo/actions/runs/37103506897)：verify 24min（原74），pytest 1184.54s，3583 passed/1 failed/3 skipped；失败为test_docs_notifications工作簿字节跨秒脆弱性（3488e6d修复）。
- WP9（并行会话，api→deferred→files 13提交）两轮独立安全审查：第一轮无机器凭据到达Agent/付费/同意/会话或绕过scope的路径，6项修复已并入；第二轮无阻断，1项应修（standing对畸形principal放行）与3项注记已交修。合并后本地全量4454项/1014s，0失败/错误，2环境跳过，峰值801MB；五守卫、typecheck/build、6个npm工作区、SDK34+14、infra unittest通过；扫描无秘密；推送cf1bb32。

### 2026-10-03 · 模型流量、B1 链路底座（af68968 已推送；ab69741/0e95fcc/d2e1f15 本地）

- 27ab127 模型调用 RPM/TPM/付款人在途/公平有界等待：核心 24 项 + 组合 4 项 + 管理 API 176 项 + 前端 58/71 项通过；合并 WP9 栈后本地全量 4533 项/1077s，0 失败/错误，2 环境跳过；门槛全部通过后推送 af68968。
- B1 只读调研（子代理）：发现异步 partial/failed/cancelled 被当成 unknown、Docs 无数据输入入口、生产 portal 角色无 Message 发送、unknown 发送无法核销等缺口。
- ab69741：partial 完成步骤并可用 outcome.<step> 分支，failed/cancelled 走 on_error，只有真正未知才需核对；9 项新测试中 5 项在旧引擎失败；Suite+integration 1784 项通过。
- 0e95fcc：Message 发送在所有角色改为与收据同事务写 outbox，由 Message 进程以本人身份投递（访问、对象讨论、归档、受众、账户再核对，run id 去重），收据 ticker 只按真实结果结算；20 项适配/桥接与 63 项 Message 测试通过，Suite+integration 1785 项通过。
- d2e1f15：docweave.project.from_dataset 以模板项目为每次运行建立固定模板版本的新项目与唯一输入（DataPrep 字符串单元格 XLSX，经 Docs 原有接纳链），缺映射列在创建前拒绝；3 项新测试与 25 项相关测试通过。
- a1c024b 本机全量：4548 passed / 2 skipped / 1274s；五守卫、typecheck、build、infra unittest 通过；npm test 首次失败（account refusals：bridge 经 capabilities 可达）→ 67b08c4 修复后 58/58，相关套件 1293 passed；推送 67b08c4
- 跨空间数据流（未提交）：test_data_flow 8、test_agent_transfers 4（C1 读两空间比较、只向合法目标写并准确说明阻塞；C2 卡片显示来源/目标、真实记录与 delegation.transfer_from；政策变化使确认失效；C4 撤销 B 后待确认写入 409、A 已完成部分保留、依赖 B 的会话内容隐藏、转移所得记录仍由 A 成员可读）、组织上限 1；Suite agent/forms/flow 子集 329 passed；变异：关闭 Agent 转移判定 4 失败、关闭 Forms 判定 1 失败、关闭流程判定 1 失败、同时关闭确认与执行复核 1 失败


### 2026-10-03/04 · 协调者交接后的基线（会话 80146fec；未提交树 = 67b08c4 + data_flow + CR-039）

- 交接：用户 2026-10-03T14:00Z 中断原协调者 df1f0b15 并取消其循环；15:01Z 本会话接管锁。三个原会话均书面确认停止并交接（data_flow 无半成品、32 个文件均应提交；admin-governance 末次修改后未复测、无浏览器验收；Webhook 分支有 9 项已核实未应用的审查结论、两路审查未完成）。
- CI：67b08c4 verify 失败（Actions 37124810853，1 failed / 1574 passed / 3 skipped 后按 -x 停止，deploy skipped）。根因是测试断言撞上随机请求 id（CR-039），非产品缺陷；修复后该文件 74 passed。
- 本机全量（2026-10-03T15:16–15:34Z，五套件并行，`scratchpad/wt-test.sh business-apps <suite>`，PYTHONPATH 指向本树）：apps/docs/tests 1488 passed / 2 skipped；services/identity/tests 1206 passed；apps/messages/tests 63 passed；apps/suite/tests 707 passed；tests/integration 1098 passed。合计 4562 passed / 2 skipped / 0 failed。
- 其余门槛（同一树，2026-10-03T16:00Z 前后）：check_boundaries / check_brand / check_design / check_component_names / brand_assets --check、npm run typecheck、npm run build、npm test --workspaces --if-present、infra unittest（8）、Python SDK（35 passed, 14 subtests）均 exit 0。
- 未运行/未验证：本会话尚未做浏览器验收（原协调者以带标注的脚本化模型替身在隔离实例完成过 data_flow 四项浏览器核对，属其证据，非本会话复验）；真实模型链路未验；CI 对新树未运行（尚未提交推送）。
- 进行中：data_flow 两路独立只读审查、admin 运行页一路只读审查；Webhook 分支 step 2 已本地提交 930a954（基 27ab127，未变基、未推送），工作流 wf_f4d87c36-c38 依次应用交付侧/管理侧审查修复并补“授权与数据暴露”“核心改动影响面”两路审查与对抗式核实。

### 2026-10-04 · data_flow 审查修复与推送（e6018ff、a407c2c）

- 独立授权审查（只读子代理）在未提交的 data_flow 上报告 DF-A1–A10，并给出 13 项通过的复现脚本（scratchpad/agents/dataflow-authz/repro）；协调者本机复跑 13 项全部复现。修复后 10 项断言旁路的脚本失败，其余 3 项为不依赖旁路的探针。
- 新回归测试：test_data_flow.py +8、test_agent_transfers.py +2、test_organizations.py 版本前置条件断言。变异脚本 scratchpad/mutate_df.py 逐项关闭 13 处保护，对应测试全部失败（首轮 2 项未捕获 → 补强“相同取值的过期修订”和“非零过期修订的条件写入”断言后捕获）。
- 提交前同一树门槛（2026-10-04T00:25–00:48Z）：apps/docs/tests 1488 passed / 2 skipped；services/identity/tests 1206；apps/messages/tests 63；apps/suite/tests 717；tests/integration 1098；合计 4572 passed / 2 skipped / 0 failed。五守卫、npm run typecheck、npm run build、npm test --workspaces（19/41/145/25/72/58 全过）、infra unittest 8、Python SDK 35 均 exit 0。
- 过程中的两次失败及处理：account-web refusals 测试要求新拒绝语句有三语言文案 → 已补；apps/suite 收集错误（test_flow_trigger_visibility 按路径加载 test_flows，新增的相对导入不可用）→ 去掉该导入后 717 passed。
- 推送：用户 2026-10-04 指令“直接推送”。推送 feat/business-apps（PR #1 草稿）；未推 main、未部署。推送时正确性审查与三路对抗复核尚未返回，其结论另行处理。
- 未验证：新 SHA 的 CI；本会话未做浏览器验收；真实模型链路。



## 2026-10-06 · Codex 接管 Agent 计费与真实上游验收（进行中）

基线 e0a22de，生产 a4b42c8；检查时间 2026-10-05T17:14:39.795333+00:00。本轮补丁尚未提交，旧报告不自动算当前发布证据。

- 主分支保护读取 API 返回403（私有仓库计划限制）；没有更改保护。无打开 PR。生产 current.json 与四应用角色镜像相同，gateway 与 ClamAV 健康。
- 上游只读 `/models`、`/user/balance` 200，模型 `deepseek-flash` 与 `deepseek-v4-pro`；没有记录密钥或账户金额。证据 `/private/tmp/synwevo-deepseek-account-20261006.json`。
- 官方 DeepSeek adapter 2 个真实请求、read_utc_clock 工具返回实际时钟并正确复述：prompt 300+356，completion 25+19，第二调用缓存命中128；两次 trusted_terminal。证据 `/private/tmp/synwevo-deepseek-live-check-20261006.json`。沙箱内第一次未解析 DNS，以 curl 证实后经正常网络权限重试；不是对未知已派发请求盲重试。
- 隔离真实工厂：初始空间未启用 AI，目录返回 ai_processing_allowed=false，模型没有读取内容；通过 agent.workspace_policy.set 为隔离空间明确允许 DeepSeek 后，读取实际 Forms 列表；用户式浏览器报价/有限预算确认、真实 SSE、钱包预留和结算通过。
- 第三条真实运行发现/修复产物问题：草稿详情通过已登记资源路径生成内部链接，无需发布/公开分享；点击“打开产物”到真实未发布表单编辑器，原始草稿未变。7 次模型调用结算7笔；测试资金为一次管理员隔离赠送加欢迎额度，不是虚构付款。
- 只读独立审查发现三处并发缺陷（Popper），已交其修复；此前132项相关测试通过不能代替并发回归。完整旧门槛恢复结果 `/private/tmp/synwevo-agent-billing-verify-e0a22de-n1vgaoj3/summary.json`；Docs 子进程本地包未装引发1项失败，正在补验。
- 当前链接/来源策略/Forms 定向106项通过（日志 `/private/tmp/synwevo-agent-links-tests.log`）；Suite typecheck、145前端测试、Suite及Workspace build通过。截图 `/private/tmp/synwevo-agent-live-20261006/screenshots/form-from-agent.jpg`。
- 模型文本仍可能输出英文前置句或技术字段；业务工具卡已本地化，不将提示词改善宣称为输出语言绝对保证。

### 2026-10-06 收尾验证（Hong Kong；e0a22de + 本轮待提交补丁）

- 三处计费竞争修复的完整定向集 153 passed / 7 deselected；覆盖多 Store、线程/进程、webhook 与补跑交错以及配置撤销/关闭/改价。默认权限和不可变账本不变。
- Docs 原唯一失败是隔离 venv 未安装本地包；安装当前 9 包后 pip check 和精确失败项 `test_run_py_actually_calls_the_guard` 通过。组合结果 1498 passed / 2 skipped（不是重新全跑）。证据 `/private/tmp/synwevo-docs-gate-close-ljtnhwiv/summary.json`，Docs 源码与 e0a22de 一致。
- 当前 Agent/Forms/来源策略定向集 106 passed；Suite 前端 145 tests、typecheck、build 通过。当前 workspace 外壳 build 通过。
- 真实浏览器：OIDC 登录，三次模型运行及工具结果，打开未发布表单的真实内部页面；返回/刷新/服务重启后仍为 7 笔结算。钱包显示可用 CNY1.948249、已结算 CNY0.051751、冻结 0，与 7 笔账本一致。初始 CNY2 来自隔离审计赠送与一次欢迎额度，不是支付。
- 手机 390×844：付款设置改为可键盘展开的原生 disclosure，聊天区域由约 66px 增至 267.5px；document scrollWidth=clientWidth=390。简/繁/英切换不改变用户中文内容。英文与繁体、真实长对话均检查；无控制台 error/warn。减少动态效果尚未通过浏览器环境实测，不据 CSS 宣称验收通过。
- 截图 `/private/tmp/synwevo-agent-live-20261006/screenshots/agent-phone-en-after.jpg`、`form-from-agent.jpg`；测试钱包充值按钮准确显示未接入支付、未创建订单。
- 模型有时仍输出英文工具前导或内部字段名；已加强提示，但不将此宣称为语言输出完全受控。

### 2026-10-06 CI 本轮修复

- fbf4e9cb05eb860cf23be4ec4b7deedb38499b8c 已正常推送，实际远端引用为 main；未创建 PR。Actions 37348318532 在 Account 前端错误文案覆盖检查失败，deploy skipped，生产未变化。新增 payment_revision_conflict 在普通账户页没有三语言句子；已补全公共错误目录与代码映射，未修改测试或门槛。
- 修复后 Account 前端 121 tests、typecheck、build 通过；日志 `/private/tmp/synwevo-account-ci-fix-tests.log`。失败原日志 `/private/tmp/synwevo-ci-37348318532-failed.log`。待新 SHA CI 后方可发布。

### 2026-10-06 补充真实模型与跨空间证据（e4a782e）

- 低思考模式官方 SSE 工具调用与 native reasoning replay 两次通过；23 reasoning tokens 包含在 completion 计量中；仅保存计量/重放存在标志，不保存或展示思考内容。
- 同一用户 owner A / viewer B 的真实 DeepSeek 会话成功读取、比较两边表单；3 次模型调用结算一次，各工具卡标出实际空间。同用户调用 B 写操作被服务端 403/read_only_role 拒绝且记录未变。此时累计4个 Agent 运行/10个调用/10笔结算，另有4次独立 adapter 验收调用。
- 可持久核对的脱敏 JSON 与截图已保存 `docs/agent-work/evidence/2026-10-06-deepseek/`；桌面实际1280×720、手机390×844，无横向溢出，跨空间页面控制台 error/warn 为空。
- 五个指定正式域名发布前 /readyz 均为 ready；Accounts/Docs/Message 扫描器正常。当前电脑内置浏览器对正式域名报告客户端拦截；curl公开健康可达。生产完整已登录浏览器验收仍需要可用会话/浏览器访问，不据健康码冒称完成。
- 新 CI https://github.com/WeGreen-AI/Synwevo/actions/runs/37349048194 已通过 Verify and build，正在部署容器运行时全量验证。尚未将其标为通过或部署。

- 2026-10-05T17:57:14Z：备份 `/opt/synwevo/config/runtime.env.before-agent-config-20261005T175714Z` 后，仅预置 `SYNWEVO_AGENT_PROVIDER=deepseek`、`SYNWEVO_BILLING_ENVIRONMENT=live`、CNY/scale6 钱包单位。保留五应用名单、不加入Agent；未启用欢迎额度、未配置密钥/价格/支付、未手工重启，交由当前CI/CD加载。正式定价与商户仍待批准配置。

### 2026-10-06 后续门槛发现（未部署）

- `e4a782e` 的 Actions37349048194：构建通过，部署镜像3634 passed / 1 failed / 3 skipped（28m24s），失败为 `test_machine_contract.py::test_everything_the_applications_serve_is_published_as_built`，Forms get描述已变但OpenAPI产物未重生成。deploy skipped。用官方 suite_contract 生成器同步后本地4项通过；未修改断言。
- 真实模型写入 `turn_f60df7f1fe62` 经页面具体确认创建 `frm_7aaa0ee3b939` 一次，Forms编辑器可打开，未发布、无公开链接。但旧列表快照整体哈希因新增项改变，导致回答失败 source_access_changed。隔离记录12调用/12结算；此失败如实保留，不重放创建。证据 `evidence/2026-10-06-deepseek/agent-write-before-fix.json` 及截图。修复与新增回归进行中。
- 公开文案改为“尚未取得”书面答复，避免声称已经联系提供方；简繁英同步。Portal没有npm test脚本（首次调用明确报missing script），已跑其typecheck；Suite145项与typecheck通过，最终build待合批。

### 2026-10-06 读后写修复验收

- Forms列表来源新增稳定原条目ID证明，复查只投影原集合并核对原完整哈希；任一原项删除、改变、权限或字段被过滤仍拒绝。其他操作与历史仅哈希证明保持严格检查，不迁移猜测旧集合。新增18项、关联153项通过；Python3.12新增来源+机器契约+公开状态26项通过。日志 `/private/tmp/synwevo-agent-provenance-ptp1zkv8/`、`/private/tmp/synwevo-final-source-targeted.log`。
- 新隔离会话 `conv_b61622672e73`，真实DeepSeek先读Forms列表，具体确认后新建含名称/备注字段的草稿 `frm_69612e61a3f8`，最终turn_f190d1050d64成功；可从产物卡打开Forms编辑器。刷新后同名草稿仍仅1份、余额不变、无浏览器警告/错误。此会话4次真实模型调用；隔离Agent累计16调用/16结算，另4次适配器调用。测试钱包累计余额1.901426 CNY（初始2），不是支付收款凭据。
- `evidence/2026-10-06-deepseek/agent-write-recovery.json` 绑定engine源码SHA256，真实模型输出的步骤说明仍可能英文，最终回答简体中文；未承诺模型语言完全一致。未发布草稿、未发客户通知。

- 最终修复提交 `734736a2fef5788152343707126aa5b4f8e55462` 已推送main。GitHub同时出现同一SHA/main/push/workflow生产流水线37355089828（执行）与37355090298（排队）；已取消后者并核实completed/cancelled，保留前者完整验收和部署。未改变工作流门槛。
- 独立发布复核24项通过（契约15、三语言文案3、guards3、未配置密钥的隔离启动3），无网络请求或计费记录；证据 `/private/tmp/synwevo-release-review-e4a782e-dr3x0add/summary.json`。

- 官方 API 联系页核实支持邮箱；已在 DEEPSEEK_ENABLEMENT.md 准备不含密钥/客户数据的书面询证草稿，未发送。公开页面没有承诺已获答复。

- 对既有16次真实调用做只读测量，未增加付费请求：账本的派发→用量证据时间戳差中位1秒、最大2秒，时间戳分辨率1秒（0不是零延迟），仅小样本串行验收，不等同首token/请求端到端/生产压测。`evidence/2026-10-06-deepseek/call-latency.json` 明确测量口径和限制。

### 2026-10-06 CI 容量门槛

- Actions37355089828 / 734736a：Verify and build通过；完整镜像pytest在81%处因runner磁盘耗尽中断（No space left on device，2026-10-05T18:58:17Z），不是完整测试通过，deploy skipped。精确日志 `/private/tmp/synwevo-ci-37355089828-failed.log`。准备有证据的临时目录/构建占用修复；不删测试、禁检查或将重跑当修复。

- 容量根因对照（同一20项真实Identity测试，均20 PASS）：默认all保留策略结束67.20MiB/峰值71.31MiB/20个目录；原生failed结束6.11MiB/峰值13.39MiB/0目录。空Store含868个schema对象、checkpoint后3.05MiB；每项一个Store的4176项容量推算约12.46GiB，明确不是故障runner实测。测量 `/private/tmp/synwevo-ci-disk-734736a-9slw2fr1/`。原生failed会丢setup/teardown失败现场，故采用完整三阶段成功后清理的方向，不减测试。

- CI容量修复：新 `--ci-clean-passed-tmp` 仅在 CI=true/native retention=all 时启用，完整三阶段全通过后才删除 pytest 原生函数级目录；失败、跳过、自定义、符号链接及会话数据不删。独立测试覆盖根清理失败、setup/call/teardown失败及本机默认不变。Python3.12.6/pytest9.1.1运行infra共11项通过，证据 `/private/tmp/synwevo-ci-disk-u6bwuik9/production312-infra.{log,json}`；只读独立复核3项通过，`/private/tmp/synwevo-retention-review-zqkr65kf/{retention-tests.log,binding.json}`，前后源码哈希一致。CI专属合成数据挂载提供有限磁盘诊断；保留全量测试、网络隔离、2CPU/3GiB门槛，未扩大机器清理范围。失败数据只保留至runner销毁，无 artifact 上传声明。

- 2026-10-05T19:23:01Z：按用户免费“1块”要求，生产 `runtime.env` 预置 `SYNWEVO_BILLING_WELCOME_MINOR=100`（每个已验证账户/邮箱一次CNY1），保留既有每日200次总量及事务幂等限制。备份 `/opt/synwevo/config/runtime.env.before-welcome-20261005T192301Z`；只增此项，Agent仍不在应用名单，未重启、未赠送，随CI/CD加载。币种解释已向用户说明，正式售价仍由管理员发布。

- 发布前只读检查：2026-10-05T19:29:56Z 主机可用磁盘180.32GiB，仍运行a4b42c8；19:41:31Z接收器原有阻塞队列计数 jobs/webhook_deliveries/mail_outbox 均为0。此为切换前快照，不替代接收器正式切换时复检；未删除缓存、镜像或业务数据。

- 2026-10-05T19:57:24.181190+00:00：961acbf / Actions37362720332 的verify作业111940913669已completed/success，构建与完整镜像测试全部步骤成功；deploy111952838389处于queued。CLI在整条run未结束前尚不能提供日志，测试具体数量待最终日志核实，不从旧结果推断。

- 961acbf准确CI日志：完整镜像5130 passed / 5 skipped / 242既有弃用warnings，1823.45s；verify作业34m14s。6前端工作区496 tests、infra 11项及SDK/守卫/类型/构建门槛通过。CI按全部三阶段成功条件清理4110个临时目录，结束仍10461MiB可用。5个跳过原因是镜像内无Node（2）、无Docker（1）、未设置隔离clamd（1）、项目未选定LICENSE（1）；Node真实协作另在宿主verify步骤通过。安全摘要 `evidence/2026-10-06-deepseek/ci-verification.json`，原始日志 `/private/tmp/synwevo-ci-961acbf-verify.log`。部署作业暂无审批项，等待ubuntu-24.04执行器，不代表已部署。

- 2026-10-05T20:04:03.242167+00:00：部署job111952838389持续queued、runner_name为空、steps为空；pending_deployments为[]，没有人工环境审批项。官方 https://www.githubstatus.com/incidents/3q1yb5m7ltvb 报告Actions hosted-runner分配延迟（19:11Z开始，19:15Z说明）。此与本作业排队一致，记录为外部依赖，未取消/重跑/改runner或走手工部署旁路。

- 2026-10-05T20:08:49Z最终只读核对：生产仍a4b42c8、五服务healthy，961acbf部署未发生；release-status.json保存verify成功/deploy queued，production-before-pending-deploy.json保存实际运行版本。官方Actions故障仍investigating（20:08:48Z读取，官方最后更新19:50:50Z），见github-actions-status.json。保留原部署作业，停止本机重复轮询并保存断点；新版本上线核对脚本已准备，未误执行于旧版本。

- **最新覆盖20:10:16Z**：job111952838389 completed/cancelled、runner为空、steps=[]；check-run注释 `The job was not acquired by Runner of type hosted even after multiple attempts`。整个Actions37362720332为failure，verify保持success。前述queued是当时快照；部署现已被平台取消，不会自动继续，也没有执行SSH部署。没有重跑故障平台上的同一操作。STATE写明待故障恢复后仅重跑该deploy job；本地watch自行以exit1结束。最新状态已更新release-status.json。

## 2026-10-06 恢复远程部署

- 2026-10-06T01:34:15.145714+00:00：用户明确要求远程部署。现场工作树干净、HEAD357ce92，961acbf之后仅文档修改；生产current.json仍a4b42c8，没有在途生产workflow。官方Actions组件已operational；原run verify成功/deploy cancelled（无runner）。重新取得单协调者锁，执行 `gh run rerun 37362720332 --repo WeGreen-AI/Synwevo --job 111952838389` 成功，沿用同一源SHA，未启动手工部署或重放业务侧副作用。

- **部署完成2026-10-06T01:40:51Z**：同一run attempt2/deploy112064193905 success，受限SSH步骤返回部署镜像961acbf及备份20261006T014023Z；公开健康步骤成功。attempt2所列verify112064194680复用10月5日原verify111940913669结果，不是本轮重新运行5130项。原始日志 `/private/tmp/synwevo-resumed-deployment.log`；脱敏结构化记录 `release-status.json`。
- **独立核对01:41:34–40Z**：四应用镜像ID `sha256:1358526d4d1776dcf8b28cb115f65c0fb654fbbc2694a3ea3076278d3d8bd14e`、OCI revision与current.json均961acbf；accounts/docs/portal/messages/gateway为running且health精确为healthy。备份manifest来源a4b42c8，数据库存在；未做恢复演练。生产billing_calls/settlements/model_provider_versions/payment_settings_versions四表存在且均0；Accounts与Portal加载DeepSeek/live/CNY(scale6)/welcome100，Agent未列入名单。见 `production-after-deploy.json`。
- 五个指定域名 `/readyz`、根页与根页引用JS均200；所适用的数据库/存储/worker/扫描器正常；匿名模型提供方与钱包充值选项401；工作空间OIDC起跳303至accounts.synwevo.com/oidc/authorize。见 `public-after-deploy.json`。检查脚本均exit0，无生产业务写入；没有重放模型请求、赠送、表单创建或支付。
- 本次远程代码部署已完成，正式模型与收款配置仍须既有真实管理员重认证及商户信息。Agent没有公开启用，未完成已登录生产业务/支付验收。后续文档提交不改变运行产品源SHA，不触发重复发布。

## 2026-10-06 CR-045 来源转出政策与B1补充验收（本地，待CI）

- 基线HEAD47c601e，部署源961acbf；本轮只读核对Actions37362720332仍success、远程main47c601e。没有重部署旧源、重放模型/付款/客户通知。协调者锁由Codex当前会话持有。
- 复制先复现：`test_request_refused_before_manifest_is_sent_to_target[none-…-browser]`预期403却201（`/private/tmp/synwevo-copy-policy-before.log`）。修复后UI/API申请前、审批及worker认领后事务内检查来源政策；拒绝/撤回可用；已完成副本独立。
- 组织发布现在冻结head与来源/全组织受众政策在同一事务；未知政策、无导出权、撤权与冻结均拒绝。合法已发布版本仍可使用。二次复核发现仅目标空间委托可借真人源权限发布新bytes；新测试先失败（`/private/tmp/cr045-org-delegation-before.log`），随后增加显式源委托检查，保留合法UI跨空间维护与已发布组织资产复用。
- Python3.12：7个相关文件88 passed / 0 skipped / 22既有依赖弃用warnings，33.26s（`/private/tmp/synwevo-cr045-py312.log`）；前端6工作区合计496通过，类型和全部构建通过。新增拒绝句与目录完整性两次发现翻译遗漏，修复后Docs145通过，其余351无需重复。边界/品牌/设计/组件名/Logo22/应用图标36守卫及diff通过。第一次资源检查误用系统Python缺Pillow，改用已锁定3.12环境后通过，未调整门槛。
- 真实浏览器：隔离服务37771，合成账户真实登录；三语言各一次UI复制申请返回403，组织模板UI发布一次403；数据库复制申请/事件/副本/组织发布/版本均0。390x844 `scrollWidth=390`，对话框374，reduced-motion=true，Escape回焦点。控制台只有对应预期403及登录前401，没有JS异常。四张截图已目视核对，见`evidence/2026-10-06-transfer-policy/`。不替代生产登录验收。
- B1–B6只读差距审计未发现新的产品缺陷；M1/M2/M4补充为同PDF哈希与段落来源、确认版本/逐行问题谱系、必填附件缺失/提取冲突阻断、真实消息写入后回执投影中断的安全对账、等待期间过期审批提示。7 passed / 0 skipped / 6既有warnings，6.393s；业务服务、PDF解析与worker未替换。`b1-test-summary.json`绑定文件哈希；故障注入仅明确的事件提示/回执更新。实际服务进程重启及同运行UI核对尚在执行，不写成通过。
- `local-verification.json`保留本地源文件SHA256、命令/日志与浏览器计数；提交/CI/部署后追加准确版本关联。

- 2026-10-06T02:23:12.556057+00:00 最后读取链复核补充：组织审批列表原来向非目标成员暴露清单。新增5场景中4项准确复现泄漏/分页范围，另一项夹具错用不存在的 suspended 状态，已纠正为 frozen，未削弱断言。列表在LIMIT前按有效目标成员过滤，详情/决定前复查成员及空间状态。最终Python3.12关联93 passed / 0 skipped / 22 warnings，33.32s（`/private/tmp/synwevo-cr045-py312-final2.log`）；源码哈希已更新。浏览器cr045已关闭，B1服务验收继续。

- 2026-10-06T02:24:57.809299+00:00：30个任务内文件通过暂存秘密标记/敏感路径检查，正常提交推送`f1dae29ed028cc0c38aacb03dccc6da1f76047e6`，远程main快进；[Actions37403925697](https://github.com/WeGreen-AI/Synwevo/actions/runs/37403925697)正在运行，发布仍取决于该SHA完整verify门槛。未手工部署或绕过审批。

- 2026-10-06T02:31Z：准确f1dae29代码的正向浏览器补验复用原隔离库；合成源组织政策由none改organization后，通过真实登录/空间切换/模板菜单发布一次。刷新后仍为发布1/源版本1；Store核对organization_templates=1、releases=1、project_copies=0。截图与allowed-publication.json已保存。不使用该结果替代跨空间个人Agent或生产验收。

## 2026-10-06 B1/B3/B6 实际进程恢复与同运行浏览器

- 源f1dae29的隔离服务在waiting_approval/review时实际停止PID49789，启动新PID56148后同flowrun_45fa79e79365 / apr_9a2c84d770a9仍等待，独立审批人owner以真实页面登录并批准一次，实际后台自动完成Docs/Message。没有重启后手动tick，未调用外部模型/支付或通知客户。
- 最终partial/end，1项目/1任务/1真实DOCX/1业务消息，所有有副作用操作各一次；部分完成来自应保留的1条金额审核问题。页面刷新及深链接后再次Store计数一致。Forms/Extract/DataPrep/Tables/Approvals/Docs/Message与个人运行历史均以同ID核对。
- PDF/Extract/异常清单/结果/DOCX全部经浏览器下载，PDF SHA256一致、提取页1行1/2、人工确认revision2、处理后12500.50、实际DOCX为Invoice INV-B1-0042和Amount 12500.50。截图含390px Message（无横溢、减少动态效果），已逐张目视；Flow控制台0异常。Message可选产物卡探测404 no_card如实记录，原链接下载正常。
- 持久证据 `evidence/2026-10-06-b1-restart/`。准备阶段复用真实服务夹具/受控时钟；不是全链路纯UI创建，不冒称生产业务或备份恢复通过。首个临时seed缺macOS主入口保护的失败数据保留；修正脚本使用明确新隔离运行，生产实现未变。代码补测7项仍绑定f1dae29。

## 2026-10-06 f1dae29 远程发布完成

- Actions37403925697 / 源f1dae29：verify112077115701 success（02:23:56–03:19:09Z），deploy112091089338 success（03:19:14–03:21:10Z）；既有受限SSH自动发布，无手工替代部署。gh run view作业日志意外输出空文件，改用只读Jobs logs API取到实际日志，未据空日志宣称测试数量。
- 07:07Z 独立远程核对：current.json、四应用OCI revision一致f1dae29，镜像sha256:22607710799da37fdc6a42b977f3a1ab9ef1dd8cd3ae042e0f17f1f2b1270298；accounts/docs/portal/messages/gateway running且healthy。恢复点/opt/synwevo/backups/20261006T032043Z存在，来源961acbf及数据库已核对，未做恢复演练。
- 五正式域名readyz/根页/JS均200，工作空间OIDC起跳303至Accounts，匿名模型提供方/充值入口401。生产model_provider_versions/payment_settings_versions/billing_calls/billing_settlements仍0，Agent未开放；DeepSeek/live/CNY/欢迎100分配置已加载。不把部署等同正式模型/收款开放或生产已登录业务验收。
- 证据 `evidence/2026-10-06-transfer-policy/{release-status,production-after-deploy,public-after-deploy}.json`。其后Admin D11/D13/D16修改仍仅本地，不属于本次上线。

- f1dae29实际完整镜像测试5191 passed / 5 skipped / 242既有warnings，2973.10s；前端496及SDK/infra/守卫/类型/构建通过，CI结束仍10456MiB可用。运行镜像跳过项与前版相同并在ci-verification.json列明。


## 2026-10-06 Admin D11/D13/D16 发布前验证

- 源f1dae29加当前修复，源码SHA256、隔离真实UI及数据计数见 `evidence/2026-10-06-admin-continuity/verification.json` 和对应JSON/截图。
- D11 25预期失败后127独立项通过；D13审核侧69通过，独立复核补发现创建竞态，追加15预期失败后48项通过（有测试重叠，不累计为独立总数）；D16最终112通过，Docs任务29通过、旧token禁用→启用仍拒绝1通过。所有这些最终组无失败/跳过，既有依赖弃用警告保留。
- D16初次将入场reauth移入锁导致6个既有竞争用例失败，已恢复入场检查并保留锁内复核；Docs事件早于审计提交的新增2项先失败后修根因。没有改断言避错。
- 真实浏览器/同一隔离数据：凭据重认证后一次启用；唯一组织所有者停用预览409，账户active；审核重启后同举报，removed/消息正文清空/审计1/通知1/待办done。三语言确认、390px无横溢/减少动态效果/键盘提交、截图目视通过。不涉及真实客户或外部付费调用。
- 前端Account121、类型/构建通过；模块边界、品牌、设计、组件名通过。B1原始证据中的稳定Docs操作ID/路由按仓库规则增加有理由的STABLE分类；系统Python缺Pillow的Logo检查改用锁定Python3.12，22文件通过。未安装替代依赖或降低门槛。
- 浏览器预期401/403/409、未开放组织付费503、测试进程重启短暂断线逐项记录；无JavaScript异常。当前只是本地验证，提交、CI、生产版本需分别追加。

- 最终扩展Message回归118独立项通过：首跑104通过，另14项因沙箱不允许Clamd测试替身绑定127.0.0.1出现2失败/12setup错误；仅对受影响3文件授予回环端口后14通过（2.29s），没有改源码/断言。D13追加diff独立复核无阻断。隔离浏览器与37774服务已关闭，数据和证据保留。

- 2026-10-06T07:35:56.638341+00:00：58个任务内文件暂存秘密/敏感路径检查无发现，正常提交推送08d6700cd524da10163643dbf97f7e1a113a4bd7。Actions37430536566正在验证此SHA；生产仍f1dae29，不将本地验证或推送当作新版本已部署。

- 2026-10-06用户要求“完成这一轮后先暂停”：当前仅收尾08d6700既有CI/CD。所有四个子代理已关闭；D4/D18本地收尾证据见evidence/2026-10-06-paused-followups，源码未提交、未包含在08发布中。D20未改码。新测试服务/浏览器/worker已关闭，不重放隔离seed和业务操作。

## 2026-10-06 08d6700 发布与暂停收尾

- 既有Actions37430536566成功：verify112160107888（07:34:49–08:28:08Z），deploy112178825997（08:28:12–08:30:09Z）。准确产品SHA08d6700cd524da10163643dbf97f7e1a113a4bd7；未手工部署、未重跑业务副作用。
- 完整容器测试5282通过、5跳过、242既有警告，2862.91秒；前端496通过；Python SDK40及14子测试、TS SDK25、构建/守卫/部署检查通过。Node相关两项另有host验证，另三项为无Docker、未配置隔离clamd、未选LICENSE；详见evidence/2026-10-06-admin-continuity/ci-verification.json。4262成功夹具清理，末尾可用10450MiB。
- 08:31Z远程独立核验：四应用镜像sha256:f2a50db5681ea23776d9498818f06e15b40725295b54724687f36409b967ac54，OCI revision/current.json均08d6700；accounts/docs/portal/messages/gateway running且healthy。备份/opt/synwevo/backups/20261006T082943Z对应前版f1dae29，manifest/数据库存在，未做恢复演练。
- 五正式域名readyz、首页和首页JS均200；身份起跳303到Accounts；匿名模型提供方管理/充值接口401。生产登录后的业务修改未重放，本轮治理交互的隔离浏览器证据已固定源码哈希且与提交一致。
- DeepSeek/live/CNY6/欢迎100分配置保留；正式提供方/支付配置版本、调用/结算记录仍为0，Agent未加入开放名单。不能把代码发布称为正式收费开放。
- 用户要求本轮完成后暂停；所有子代理、临时浏览器/服务/worker均结束。D4/D18未提交代码、定向测试和私有恢复补丁保留；D20未改码。发布证据和断点使用纯docs提交保存，不触发下一次部署。

## 2026-10-07 恢复与Webhook可靠性包（进行中）

- Git/CI与11:32:35Z线上只读检查：远端e70929f，运行08d6700，四应用相同镜像/五服务与ClamAV健康；无生产业务副作用。证据 evidence/2026-10-07-webhook-reliability/production-before.json。
- D18独立审查未发现新的权限/竞争阻断；隔离30万已发送行证明清理仍全表扫描，LIMIT只限删除量。查询计划与候选索引测量在attachment-scan-before.json；是本机合成数据测试，不是生产延迟承诺。新增索引升级回归先1项预期失败（/private/tmp/synwevo-d18-index-red.log），随后修复，最终验证待追加。
- D18最终索引由真实Store启动创建：新增迁移/查询计划回归与附件、schema扩展关联共13 passed/1既有warning（/private/tmp/synwevo-d18-index-green.{log,xml}）。相同30万已发送合成行的查询中位0.017ms；加150条过期项删除100条中位0.390ms（三次回滚测量，SQLite3.53.2，本机非并发）。EXPLAIN使用ix_message_attachments_unused_expiry，无临时排序；完整条件与源码哈希见attachment-scan-after.json，不当作生产指标。
- 本轮集成门槛：受影响Webhook/API管理187 passed/6既有warnings，附件与升级13 passed/1warning，生成契约9 passed/6warnings；500前端、Python SDK45、TS SDK34与类型、根目录typecheck/build、六守卫通过。三语言真实重认证/响应丢失重试与D4默认/零/取消复验通过，所有隔离浏览器及服务关闭。verification.json绑定最终源码哈希；仍待提交/CI/部署。
- 46个本任务文件经秘密/敏感路径与diff检查，正常提交推送8ba2956750c2080d300fa17523bcc391e85cae40；Actions37619470838于12:13:27Z启动。当前verify运行，生产仍08d6700；未手工部署、未重放生产业务。
- 2026-10-07T12:29:30.386624+00:00 用户明确本次发布核验后暂停；仅完成在途8ba2956，不再开始实现。用户回复已有商户账户，法定名称/注册地仍待补充；未据此宣称已配置Stripe或真实收款通过。

- 2026-10-07T12:36Z 用户提供Stripe凭据文件后，官方账户只读检查200：live/HK/individual、卡支付和结算启用；未执行付款、创建Webhook或发布平台支付配置。用户先前“香港公司”的假设需修正为个人商户待确认，展示名不作为法定名称。脱敏摘要stripe-account-readiness.json不含密钥或账户ID，完整必要商户名称仅保存在本机0600临时摘要。

- 用户随后明确“使用这个个人商户”：以已核验香港个人商户继续，不再将香港公司法定名称作为等待项。展示名Hiko Sphere已确认用于识别该商户，不将它臆断为法定个人姓名；默认HKD也未用于改写CNY产品规则。正式支付配置/回调/真实充值仍待恢复后完成。

- 12:46Z Stripe官方支付配置/Webhook完整列表200，card available/on、alipay与wechat_pay unavailable/off，Synwevo指定回调0。证据stripe-channel-readiness.json不含其它业务URL、账户ID或密钥；没有新增Webhook、修改商户或调用Checkout。当前充值关闭，恢复后须先解决固定三渠道与商户可用渠道的差异。


## 2026-10-07 发布完成与 2026-10-08 Agent 启用准备（覆盖上文在途状态）

- Actions37619470838准确源8ba2956：verify112785797099于13:09:45Z成功，deploy112808974562于13:11:50Z成功。完整镜像5328 passed/5 skipped/242 warnings（3039.56s）、前端500、Python SDK47及38子测试、TS SDK34，类型/构建/六守卫/infra/真实Node协作检查通过；CI成功清理4308个临时目录，结束磁盘10366MiB。ci-verification.json列出5个跳过原因。原始verify日志SHA256为e88eb1f6db77c28c38f06d1563d8e8bf5e6db6ded329164f1a0ffc81b88bb9a1。本机watch网络错误不作为CI结论。
- 14:39Z生产独立只读：四应用镜像均sha256:9b83c825a6594924aaff67e6b3a918f9de2beb4e4d6838cf691477800305337e，OCI/current.json均8ba；五服务healthy，附件部分索引存在且真实查询计划使用。备份20261007T131123Z含DB/manifest、来源08d6700，未做恢复演练。五域首页/readyz/引用JS均200、匿名后台与充值接口401、OIDC起跳303。production-after-deploy.json、public-after-deploy.json及release-status.json保存真实时间/结果。
- source-binding.json将23个最终源码/测试SHA256与8ba Git blob核对，无产品漂移；上述证据位于evidence/2026-10-07-webhook-reliability。代码已发布，Agent激活与支付另行验收。
- 用户随后要求优先上线Agent，并完成真实管理员登录；浏览器通过现有重认证，于14:58:27Z发布分时日历v1，操作ao_d7da658ed75f。2026-10-08T01:52:38Z通过Store(migrate=False)及query_only只读复查：日历只有1版、对应admin_operations记录存在；价格草稿/已发布价格/成本策略/提供方/支付/模型调用/结算仍0，Agent未开放。不重复日历发布。
- 官方DeepSeek只读API于14:44Z确认账户可用且CNY余额正；models首次URLError，一次有界重试于14:46:51Z返回200并列出deepseek-flash。没有推理/扣费请求，也不公开精确上游余额；provider-readiness.json保留失败及重试事实。
- 2026-10-08再次查官方价格与限速文档，准备单Flash首发配置，成本为峰谷官方价、拟2倍售价、64Ki输入/8Ki输出、none/low、放行名单。真实校验器与网关上界公式核对12项约束通过（成本0.196609元/售价预留0.393217元），日历10月7日节假日与10月8日工作日切换正确。该检查没有Store、网络、资金或密钥，不替代生产发布或模型验收。证据evidence/2026-10-08-agent-activation/{activation-preparation,preparation-validation,production-state,provider-readiness}.json。
- 当前Mac锁定，已请求用户手动解锁；CUA无法继续已登录后台。未绕过管理员会话/重认证、未直写配置表，未创建新loop。断点已保存，继续动作是发布实际价格/预算/提供方及真实Agent验收；Stripe支付渠道准备不阻挡欢迎额度验证。

- 浏览器随后恢复可操作；真实后台保存成本价草稿mpd_3870d36aa953/rev1（2026-10-08T02:00:45Z），已核对CNY6、模型deepseek-flash、峰值2/0.04/8、低谷1/0.02/4与09:00HKT生效时间。获取签名预览后发布触发站内重认证，口令未自动填充，页面提示请输入口令，未发生发布；已请用户在站内用口令/安全密钥完成。未重复新建草稿，未绕过门禁。

- 2026-10-08T02:05:10Z生产只读核对草稿仍为mpd_3870d36aa953/rev1/draft，完整费率与实际保存内容一致；价格已发布数0、日历数1、模型调用数0。production-draft-state.json记录真实服务器状态，不因点击发布按钮就宣称成功。

- 2026-10-08续轮：02:15:20Z只读服务器仍为8ba2956、唯一成本草稿rev1未发布、调用0；重新取得同一草稿预览后仍要求真实管理员重认证。官方余额接口02:20:04Z返回200、账户可用，CNY余额高于准备的停止1元/预警21元阈值；仅保存比较结果，不公开精确余额。production-reauth-check.json与provider-balance-thresholds.json。§28.5明确要求有权者决定正式售价，已向用户提出具体2倍/1倍选项，未按默认值发布售价；所有生产模型调用仍0。

### 2026-10-08 支付渠道与错误恢复

`evidence/2026-10-08-payment-methods/verification.json` 记录39项支付回归、5项契约、507项前端、类型/构建/六守卫及Safari真实服务验收，source-sha256.json绑定19份代码/测试。子代理6项浏览器测试使用明确API夹具，不能当真实后台或支付验收；协调者另完成真实登录/重认证/持久化配置/钱包展示及390px截图（保留在会话）。未创建Stripe订单或付款。单句错误翻译基线已复现RangeError，修复回归通过。新配置字段发布后回退构建须保留读取支持，详见钱包文档。

03:15:14Z生产只读复查确认成本价 `price_85f4371761b6` 已发布（复用，不重放）；其他提供方/成本策略/支付配置与调用仍0，不能称Agent已启用。证据 `evidence/2026-10-08-agent-activation/production-cost-published.json`。

CR-066追加：旧客户端更新其它设置时省略渠道字段，96f73ab可意外恢复全三渠道。新测试在基线上1失败，修复后40通过；已请求取消尚未部署的37722051839，待新SHA完整CI。源文件哈希已刷新，原39项记录保留为首轮证据。


## 2026-10-08 25b084f 发布核验完成与暂停

- 产品提交25b084f0ff5d6897d3086ddda9356f1073562ef0；Actions37724156180 verify113138387470成功04:40:45Z、deploy113152450395成功04:42:48Z。正常CI/CD自动发布，没有手工竞争部署。前一96f73ab在新发现的旧客户端渠道重置回归修复前取消、未部署；最终验证只绑定25b。
- 完整镜像5335 passed / 5 skipped / 243 warnings，2994.01s；前端507、Python SDK47及38子测试、TS SDK34、类型/构建/守卫/infra/真实Node协作检查通过。5项跳过与前版一致；额外1项临时目录清理Directory not empty诊断保留，未宣称修复或删除警告。4314个成功夹具清理，结束余10365MiB；ci-verification.json含原日志哈希与跳过原因。
- 04:43–04:44Z远程只读：current.json及4应用OCI revision均25b，镜像sha256:4bc9890f5e47572f210a74c049d71d40ecac358db561e7756e544106536db177；6容器healthy，3个支付后端源码SHA256与Git绑定一致。19份代码/测试文件无漂移。恢复点/opt/synwevo/backups/20261008T044220Z的数据库/manifest存在，来源8ba匹配；未做恢复演练。
- 5正式域名根页/readyz/引用JS均200；工作空间OIDC303至Accounts，匿名治理/充值401。上述证据在evidence/2026-10-08-payment-methods。生产登录后的支付/模型操作未重放，真实UI证据仍是同源码的隔离后端验收，不冒称真实支付。
- 04:44:43Z生产治理只读：日历1/成本价price_85f4371761b6保留，提供方/成本策略/支付配置/调用/结算均0，Agent未开放。正式零售倍率待用户明确答复；用户登录及成本价发布不能替代售价决策。production-after-25b084f.json保存在Agent启用证据目录。
- 按用户“本次发布核验完成后暂停”保存断点；所有代理/隔离服务/浏览器与CI只读watch均已结束，仅提交文档证据、不触发新部署。全范围目标仍未完成。恢复先读取STATE并核对锁、Git和真实生产状态，复用已发布日历/成本价。


## 2026-10-08 继续正式开放（撤销旧暂停边界）

- 用户明确“Agent正式启用，真实充值开放，并且之后不要暂停了，持续做”；goal已active，本任务06:31:57Z独占取得协调者锁。Git main47c7876、当前CI37724156180成功，06:32:05Z生产仍25b084f、日历1/成本1、其他配置/调用0，没有重复部署。
- 06:41:55Z使用用户指定的live Stripe凭据，经官方API重新核实HK个人商户与card_payments active，完整列表确认原无指定回调；创建Synwevo专属we_1UOB1ZEMgc4r7oEcqTYP9tLI，立即disabled并GET复核。仅订阅6个项目处理事件，未修改其他回调、未创建Checkout或付款。请求具有稳定幂等键；secret仅本机0600恢复文件，证据只收录脱敏摘要stripe-callback-prepared.json。官方依据 https://docs.stripe.com/api/webhook_endpoints/create 和 /update 于本轮读取。
- 06:43:33Z官方只读country_specs/HK确认CNY在supported_payment_currencies；DeepSeek模型Flash存在/账户可用且高于控制阈值，精确余额/密钥未记录。这不是实际卡支付或模型验收，见live-readiness-resumed.json。
- 正式倍率再次给出2倍/1倍具体方案并等待明确商业决定。真实Admin窗口因用户切换、扩展连接解析和IAB客户端阻止暂不可操作，已给出model-costs稳定窗口请求；没有提取cookie、伪造会话或直接写治理表。独立只读验收路径由子代理准备，不阻止继续其他工作。

- 付款准备配置采用人民币原值入账、卡渠道、10/50/100元套餐、自定义10–1000元与有界频率；锁定Python3.12下真实PaymentSpec对disabled/最终enabled均通过。未打开Store、未调用支付；仅准备证据payment-activation-preparation.json。

## 2026-10-08 CI 临时目录解析线程竞争修复

基线产品25b084f/文档7964fd9。真实HTTP上传、解析和blob发布的确定性调度复现ENOTEMPTY；原根清理未等待App惰性解析线程。pytest清理现在在原3秒总预算内等待，超时明确teardown失败并保留夹具，没有rmtree重试或吞警告。38项定向通过（6项已有弃用警告）、4项保留策略子进程通过、六守卫通过；新增回归在原实现失败。证据及源码哈希：evidence/2026-10-08-ci-cleanup/verification.json。尚未跑新Linux完整CI；本地提交随后续功能批次整合，不单为测试清理重启生产。

## 2026-10-08T07:23:48.131696+00:00 暂停前本地状态

用户要求立即暂停。e577fc9已本地提交清理修复，无新推送/部署。通知恢复core新增12项通过；包含其在内通知原子派发/通知服务/平台配置共75项通过（1项已有Starlette弃用警告），日志/private/tmp/synwevo-notification-related-20261008.log。未执行新UI浏览器验收或完整CI。33870合成验收服务PID83471正常关闭，输出Application shutdown complete/Finished server process；没有SMTP、模型或真实支付。其他并行修复保留未提交，不能作为已验证发布。

并行任务暂停报告已收齐并保存在evidence/2026-10-08-paused：治理测试59通过/1失败，前端最终改动未复验，充值幂等缺迁移且未验证。不能把协调者之前75项已通过结果扩展为全部新增功能通过。三个子代理已关闭。

### 2026-10-08 恢复后的未发布修复

通知恢复backend/core定向120通过、六守卫通过，详见[evidence/2026-10-08-reliability/notification-backend-verification.md](evidence/2026-10-08-reliability/notification-backend-verification.md)。新增充值请求持久绑定迁移9通过，指纹在[payment-intent-migration.json](evidence/2026-10-08-reliability/payment-intent-migration.json)。以上绑定e577fc9上的未提交源码；尚未完成全量CI、浏览器或部署。

## 2026-10-08 可靠性发布前门槛与正式价格决定

最终证据见evidence/2026-10-08-reliability/verification.json及browser-qa.md。1538项账户/迁移初跑唯一失败为新管理路由未登记；补齐精确权限及无副作用矩阵，29项全矩阵通过。全前端528、类型/构建/六守卫通过。来源指纹绑定提交前源码；Linux完整CI与新版本部署尚未执行。

用户明确选择成本×2。Chrome真实管理回执显示成本策略v1/ao_50e7aad24bc5于19:31:11 HKT发布；其他正式配置继续准备，不能由此声称Agent/真实充值已经开放。

## 2026-10-08 正式治理配置和发布门槛失败

真实Admin已发布成本策略v1、两倍售价price_9fa1a7cbc022、提供方v1/凭据v1；所有者加入受控放行名单。支付v1密封保存且disabled，原唯一Stripe回调于12:12:52Z启用并复核；没有创建付款或调用模型。配置和官方只读状态见evidence/2026-10-08-agent-activation新增四份JSON。

Actions37771066812对应769a98d：构建通过，完整镜像4443通过/1失败/3跳过/78警告，1619.28s，部署跳过。失败为payment_topup_intents.request_hash未登记blob分类；其实际代码是规范化充值参数的SHA-256，不持有文件引用。补充生产LOOKALIKES说明，保留全部原断言，原3项集成检查通过。证据ci-failure-classification.json包含原日志指纹，新SHA全CI尚待。

## 2026-10-08 Agent正式启用、真实读工具及收尾修复

25b084f按operations§11.1在12:35:19Z启用Agent，portal healthy、五个原应用保留。操作备份与回执见activation-applied.json；没有手动发布待验代码。本人已同意DeepSeek处理并自行完成3次真实模型调用及Docs list/get，usage可信、成本与两倍售价精确对齐、账本无差异，见agent-budget/existing-live-read-call.json。没有新Forms产物或跨空间验收；用户明确保持个人支出暂停，没有追加付费调用。

CR-070回归先在旧实现2项失败，修复后132关联通过；全Agent415项初跑414通过/1回环端口EPERM，原测试在授权loopback后1通过。前端145、类型/构建/六守卫通过；三语言/390×844/键盘/减少动态效果见agent-budget/browser-qa.md，源码及日志指纹见verification.json。CI/CD仍待新提交。

Tables真实独立Store容量10000行、50页无重复遗漏、上限及混合批次均通过，详细计时/环境/初始验收脚本修正在tables-capacity目录；不推断线上并发性能。WP9依据已合并机器API与真实集成证据修正旧未开始状态，没有重复实现。

最新用户要求完成当前轮后暂停；不再开启后续工作包。真实充值仍需当前代码完整CI/CD、Admin重认证启用和有权付款人支付验收。

## 2026-10-08 本轮最终发布及暂停断点

Agent/充值/通知修复a41ea08经Actions37792532752完整verify与deploy成功，于15:21:52Z完成；完整镜像5453通过/5跳过/242弃用警告（2811.72秒）。生产四应用OCI/current.json、六健康容器及gateway/recovery/ledger哈希核对通过；五域readyz/JS、匿名拒绝和OIDC起跳通过。备份20261008T152122Z。精确证据见agent-budget/{release-result,ci-status,production-after-deploy,public-after-deploy}.json。

5个skip为镜像无Node的真实客户端/SDK、无CLAMD_TEST_ADDRESS、未选LICENSE、镜像无docker；Node客户端在前置真实Node阶段执行，其他限制未伪装通过。警告为依赖及旧Pillow/测试cookie弃用，无测试失败。

收尾仍未完成：生产旧失败turn的元数据恢复审计因自动审批两次超时未执行；浏览器恢复支付管理页的读取被自动审批拒绝（当前实际为无关私人PDF）。充值最后核实v1 disabled，未改变，真人重认证及真实卡支付仍缺证据。没有新模型调用、Checkout、配置绕过或手动部署。用户明确本轮后暂停，保存恢复步骤并停止新增工作。

## 2026-10-09 授权只读补验及暂停

用户要求本轮收尾暂停，并明确允许重试三个指定历史任务、个人预算及支付开关的只读核验。10:51:51Z SSH成功；Store(migrate=False)、query_only事务，仅选择状态/用量/计量字段，无聊天正文、密钥、模型或付款调用。生产current.json仍a41ea08；这不是本日全量健康重验。

三个旧任务均failed/billing_run_budget_exceeded，usage已known；各一笔既有结算和一次usage.reconciled事件，step:1 settled，step:2 released且没有预留或上游尝试。修复的是用量投影，不能宣称任务成功。新证据见evidence/2026-10-09-readonly-closeout/production-metadata.json，包含审计脚本SHA256。

发现旧状态已变化：个人预算最新v4 enabled，支付配置最新v3 enabled/credential_configured=true；本轮未修改它们，也未查询其操作者/发布时间。支付开关开启不等于已验证真实付款/签名唯一入账；保留此未验收项。不追加付费调用，不重复启用或创建Checkout。仅更新文档断点并暂停，无新产品代码、测试或部署。

## 2026-10-09 恢复后 CR-071

用户明确“不要暂停了”，重新取得本会话协调锁。基线c63079f、生产a41ea08、Actions37792532752成功；没有重复部署或启用支付开关。个人付费验收0.50元具体授权仍待回答，未发起模型调用。

会话历史同时间分页遗漏回归先4失败（最初测试导入路径错误已修正，不算产品失败），修复后4通过；Agent整体418通过/1 setup端口沙箱拒绝，同一测试获loopback权限后通过。149前端、Suite类型/构建及六守卫通过。真实隔离服务58条同时间会话50+8完整分页、9条搜索、3条归档；三语言、390px、长标题、键盘打开与刷新保持同ID通过，控制台无warning/error。浏览器reduced-motion=false，本项没有新增动效，不宣称该模式新验收。截图与源/日志哈希见evidence/2026-10-09-agent-history。代码尚未CI或部署。

### CR-072 / 2026-10-09 支付渠道只读检查（本地已验证）

- 基线b2309ef，源码哈希及命令日志见evidence/2026-10-09-payment-readiness/verification.json；60支付关联与29权限矩阵、163账户前端、类型/构建/六守卫通过。初次夹具/新接口登记失败及修复如实保留。
- 真实Stripe适配仅GET账户/配置，结果card默认可用、另两项默认不可用，charges_enabled=true。无Checkout/付款。默认配置标志不证明独立商户资格，也不证明币种或订单成功。
- 真实隔离浏览器1280×720/390×844，简繁英、键盘Enter/取消焦点、故障与恢复、草稿/刷新、无横向溢出；控制台无错误。未新测reduced-motion，未增加动画。临时服务已停，截图在同目录；测试替身只存在隔离脚本，不是生产回退。
- 生产只读：本人5笔旧创建失败（10月8日13:11Z）/0paid，v3开关开启、三渠道已选。具体历史上游错误未保存，不能归因于当前产品版本；用户通过原重认证界面调整渠道待答。
- CR-071已推送b2309ef，Actions37925001552完整运行镜像中；本项未发布。

## 2026-10-09 CR-073：文件接纳上限与扫描状态

基于5836948，App.require_upload_size覆盖上传各入口，有效上限取文件/启用扫描两者较小值；超过已知上限不写业务行/blob，不排扫描任务。capabilities/usage/SDK类型/OpenAPI同步；已有分片回执及取消保持，multipart重新传输仍受当前上限。原有扫描器实际流上限拒绝、故障重试和历史重扫继续有效。

本地80项上传/机器API/契约/治理（65旧项+最终15新项）、46扫描/重试/重扫，共126项通过。145前端、Docs类型/生产构建、六守卫通过。初次新测试夹具/错误封套识别失败与原field=size契约遗漏均已定位修正，未删旧断言，日志摘要与源码哈希见`evidence/2026-10-09-upload-admission/verification.json`。

真实隔离服务正常登录，合成CSV混合上传：超限文件即时提示，合法两文件最终ready，数据库2行69字节、0模板、0分片；没有生产数据。第二文件无需刷新从扫描到可用；首次浏览器发现来源列表只轮询parsing的旧缺陷，已修复scanning及带retry_at的恢复。手机390px英文模板标签曾遮挡错误，已修复并保留前后截图。简/繁/英、键盘Enter上传/Escape关闭、深链接刷新、无水平文档溢出、控制台无warn/error已核对。扫描器为明确隔离协议夹具，不替代真实恶意软件引擎验收；未调用模型或付款，未修改生产配置。

CR-071 b2309ef仍处Actions37925001552完整runtime；CR-0725836948在Actions37927604495串行pending。本批尚未推送，避免替换已有pending；线上版本要在CI完成后独立核对。

## 2026-10-09 CR-071准确版本发布回执

`b2309ef2b931337d931838b42d1c1a0c8c28111c`的[Actions37925001552](https://github.com/WeGreen-AI/Synwevo/actions/runs/37925001552)verify/deploy均成功。运行镜像5457passed、5skipped、242warnings，47m39s；发布于12:33Z。只读主机核验current及四应用OCI revision同SHA，六容器healthy，history/engine源码SHA256与本地一致；Agent保持启用。五域readyz/根页/JS200，匿名管理与充值选项401，OIDC303到账户源。备份`/opt/synwevo/backups/20261009T123308Z`，无恢复演练。脱敏回执在`evidence/2026-10-09-agent-history/{ci-release,production-release,public-release}.json`。未重放业务操作、未触发模型或付款。CR-0725836948随后已开始核验，CR-073d7d9201沿同一CI/CD排队。


## 2026-10-10 CR-072上线与CR-073门禁失败

5836948的Actions37927604495 verify/deploy成功，运行镜像5475passed/5skipped/242warnings，2736.61秒；13:26:43Z部署备份，13:27:11Z结束。16:04–16:05Z只读确认current/四应用revision同SHA、六容器healthy、四支付模块哈希匹配、五域健康/JS及匿名拒绝/OIDC起跳通过。Agent保持启用；未触发模型或付款。回执在payment-readiness目录。

d7d9201的Actions37931260526构建通过，runtime1failed/964passed/3skipped/78warnings，345.16秒，deploy跳过。test_upload_limits_name_the_file_and_the_limit发现旧too_large detail多了limit_bytes。已恢复旧契约，原测试未动；精确上限仍在capabilities和新scan_limit错误。失败回执与日志指纹在scan-recovery/previous-ci-failure.json。未重跑同SHA制造通过，下一SHA需完整门禁。

## 2026-10-10 CR-074扫描故障恢复本地闭环

原有无限退避、重启立即重扫和缺少人工恢复已修复：持久6次周期、deadline、总次数，耗尽后明确等待；同来源版本原子恢复和审计，下载/解析隔离不变，SDK不自动重复命令。CR-073后续补齐脱敏扩大输出的逐项失败契约，拒绝项不落blob/下载授权。

72核心、97扫描/权限、91API/生命周期、最终54旧拒绝/上传/恢复回归均通过（有重叠，不合计唯一项）；145 Docs前端、类型/构建/六守卫通过。PythonSDK55passed/57subtests、TS48passed及双严格类型通过。最初新夹具actor_kind无效和GET状态假设、两项遗漏翻译已修复；未删除旧断言。旧临时venv丢失pyvenv.cfg后由锁文件建立独立Python3.12环境，pip check通过，最终54项在新环境执行。

真实隔离浏览器实际上传合成37字节CSV，协议故障夹具6次耗尽，真实进程重启仍v1/6次且不重启扫描；服务恢复后键盘明确重试，同文件ready/v2/累计7次，只有1笔审计，刷新不重放。简繁英、390×844、长文件名、焦点及控制台无warning/error通过。fixture加速延迟不作为生产性能，非真实病毒引擎验收；未新测reduced-motion，无新增动画。截图、源/日志哈希、restart/recovered/refreshed元数据见scan-recovery。自己的tab9和临时服务36974已关闭。当前未提交部署，无生产配置/模型/付款变更。


### 2026-10-10 CR-074 CI国际化门禁

0812f3d/Actions37957593751构建与前置检查通过；完整runtime在4229passed后遇到1项API控制台三语言缺漏而停止，3skipped/78warnings，1520.54秒，deploy跳过。缺Retry Scan、恢复说明及文件大小新说明，已补原词库，原断言保留。准确回执ci-081-i18n-failure.json。生产仍最后实测5836948，不能把已推送当已部署。

## 2026-10-10 CI验证绑定纠正与CR-075

6e0287b/Actions38015163162在Verify and build阶段因词库数组缺逗号失败，runtime/deploy均跳过。缺陷由本轮检查之后的格式压缩引入；旧i18n-fix-verification.json保留并明确标记不能证明6e0287b通过。最终源码修复后重新完成6契约、163账户前端、根类型和账户构建；同一最终文件的哈希与日志记录在i18n-final-verification.json。修复过程首次复验又捕捉严格JSON尾逗号，已修根因，未改断言。

CR-075原hook三个竞态由实际编译调度测试复现（3失败/2通过），最终5专项/154前端、类型/构建及六守卫通过。浏览器真实身份/数据库/HTTP/SSE，只有读取故障、延迟和轮转周期为隔离注入：慢速切换不显示旧会话，失败隐藏内容，自动恢复重新读取，刷新同ID；简繁英、桌面1280×720、手机390×844、长内容/键盘/0横向溢出，阅读位置两次读取均927px，控制台无warn/error。数据库仍3会话/3消息/0turn，无模型/付款。手动重试按钮未点击（自动恢复先完成），未新测reduced-motion；不把这些写成通过。证据在2026-10-10-agent-conversation。隔离脚本早期import/seed状态错误与系统Python缺Pillow均已记录，最终用锁定环境；不混为产品缺陷。临时服务及tab10清理完毕，viewport恢复。

下一SHA仍须完整CI/CD，生产最后实测5836948；不重复部署或业务调用。

## 2026-10-10 生产每日风险检查（只读）

02:11:29Z，生产current仍5836948，Store(migrate=False)+query_only+只读BEGIN快照选择风险元数据，未读取正文/密钥。10月9日daily ok/within_tolerance；10月7/8日incomplete/balance_reading_missing。3条ledger_audit均ok/consistent、differences=0。近三日451条余额读取均ok，最新3秒前、monitor lease有效，无活动suspension/无breaker event。成本策略v1仍allowlist_only，支付v3 enabled且仍选三渠道。没有触发poll/reconcile/账本写入、模型、付款或配置变更。元数据与脚本哈希在2026-10-10-risk-readonly/production-metadata.json。只能确认首次完整日及已有审计结果，不能据此宣称连续数日通过、绝无异常扣费或真实付款成功。

94b2591已正常推送main，Actions38015933144进行中。CR-076既有发送按钮未检查loading/error或未知configured，独立子任务限定Chat.tsx/专项测试实施；协调者不并写这些文件。

## 2026-10-10 CR-076会话读取门槛

Chat的发送条件加入loading、当前读取error和configured未知；草稿继续可编辑，实际running取消不被此门槛阻断，仅SSE离线而已有有效快照仍可用。Turing限定两个文件实现，无提交/部署；原代码4项新回归失败，修复后40专项通过（子任务只保留对话输出，未伪造日志哈希）。协调者独立159 Suite前端、类型/构建通过，最终源码与日志指纹见agent-send-state/verification.json。

真实隔离浏览器新会话配置就绪后可发送；键盘进入已有会话、输入草稿，注入读取失败后发送按钮禁用且Enter未发送，草稿仍可编辑；SSE恢复后原草稿保留、同会话可发送。数据库3会话/3消息/0turn/0quote，简繁英、390×844和1280×720、宽度390无溢出、控制台无warn/error。未点击就绪后的发送，不产生模型或付款；取消只做组件回归，未声称真实模型取消。没有新动效或reduced-motion验收。tab11关闭、viewport重置、核对身份后终止PID41460。

94b2591/Actions38015933144前置构建门槛已通过，正在完整runtime，尚未宣称发布。CR-076下一SHA单独经过原CI/CD。

## 2026-10-10 CR-077显式业务资源上下文

基于36104a0新增Forms定义/Tables字段视图选择，同一消息跨有权空间，最多5项、固定内容快照。源对象、可见字段、成员/应用资格变化时隐藏用户消息与派生块/来源卡片，报价/持续授权准确绑定；原应用API承担权限，没有客户端自带正文或万能读取。

独立审查发现复制目标重新选中时缺carried导出约束，修复前4项3失败/1通过；补齐真实来源后4通过且审查复验关闭。最终合并171后端通过/6既有弃用警告，174 Suite前端通过，根类型、Suite构建、六守卫、diff检查通过。源码和原始日志SHA256在`evidence/2026-10-10-agent-context/verification.json`。

真实隔离浏览器合成53+1表单、1表：50+3分页、搜索、选择三个来源跨两个空间、取消报价保留草稿、确认一次测试运行、刷新不重放、链接打开真实表页；1turn/1accepted quote/1settled attempt/1settlement。随后经真实注册操作改变一个来源，刷新隐藏相关正文和卡片、禁止继续发送。简繁英、1280×720/390×844、长名称、Enter搜索、Escape返回焦点、无嵌套表单/横向溢出/控制台warn或error。截图和仅合成元数据回执同目录。

模型与支付仅明确隔离传输，外部派发禁止；不冒称新的DeepSeek产物或Stripe实付。启动时import/listener故障、一次缺合成根环境和一次只读列名错误均在生产之外、业务更新之前停止，纠正后一次幂等源更新成功，详见回执。最后v3服务PID68116、tab12已清理；个人文件上传/其他资源仍待实现，未新测reduced-motion。94b运行镜像仍测试、361排队，新切片尚未推送部署。

## 2026-10-10 94b2591准确发布与CR-077排队

Actions38015933144 verify/deploy成功，03:08:27Z完成。生产只读核验current/四应用OCI revision均94b2591206bb4d6a79a00345d4ada03f29a389de，六容器healthy；Agent仍启用。portal模块源哈希与git该SHA匹配（未用当前开发中的源码比较）。五域readyz/根页/JS200，匿名管理/充值401，OIDC303到账户源。备份/opt/synwevo/backups/20261010T030748Z；未重放模型、工具或付款。准确回执在2026-10-10-agent-conversation的ci/production/public-release.json。

36104a0/Actions38016706596开始后，正常推送已验证资源上下文b1c8a0b02f725cc4f8ddbe527db18c56bf954b66，Actions38019575923排队。个人文件仍本地开发，不在此提交内，不能宣称已部署。

## 2026-10-10 CR-077生产核验

36104a0与b1c8a0b的完整CI/CD均成功。11:25Z独立只读核验b1c8a0b：current/四应用OCI revision一致、六healthy、五个Agent/账本源码哈希与git该提交匹配；五域readyz/根页/JS200、匿名管理/充值401、OIDC303到账户源。备份20261010T045143Z。准确回执在agent-context的ci/production/public-release.json；无生产模型/付款或配置变更。

## 2026-10-10 CR-078个人文件本地闭环

新增user归属的真实文件接纳/严格读取/预览/下载/删除、有限租约队列及配额，引用绑定报价/运行/持续授权与受控来源；原始字节有界inline存储，与清理/账户删除同事务。Docs支持实际验证的UTF-8 TXT/MD/CSV/TSV、纯文字PDF，拒绝不完整/含图/超限内容；没有OCR或模型解析回退。

Newton独立审查发现过期清扫窗口、旧账户删除兼容两项并复验修复；26项及补充旧连接/晚到结果/rollback脚本通过。最终协调者248后端及21Agent/API三语契约通过，各6既有弃用警告；197 Suite前端、根类型、Suite构建、六守卫通过。浏览器发现小数秒响应验证错误，已修根因并补真实形态回归，没有改弱计数校验或旧断言。

实际隔离浏览器：合成1305字节文本真实上传/读取，前导零和末行/超千字长行完整，简繁英、390×844/1280×720无横向溢出，Escape回到个人文件按钮，控制台无warn/error。原下载与输入字节哈希相同。报价取消保留草稿/文件，接受测试预算后1turn/1quote/1settled attempt/1settlement；刷新不重放。删除后inline字节/解析/用户文件快照清除，问答与引用隐藏、继续发送禁用。计数/截图在agent-files，源码/日志指纹同目录verification.json。模型付款为明确本机测试传输，外部派发禁止，不是新的真实DeepSeek或Stripe验收。

浏览器文件选择/下载工具等待较长后复查Git/CI，源码未被其他任务修改。未新增真实推理/付款、未新测减少动画或生产解析性能。tab13关闭、viewport重置，PID3794已核实并终止，子任务全部关闭。下一SHA仍需原完整CI/CD，当前附件未发布。

## 2026-10-11 Claude Code接管与f919d2f CI阻断修复

接管核实：实施树HEAD与origin/main均f919d2f；Actions38054090562 completed/failure（verify失败于Docs `refusals.test.ts:338`，deploy未执行）；Codex持有者01a0f104最后活动2026-10-11T13:42:53Z为生成交接文件，两个子线程均为只读核对后task_complete，无codex实施进程；锁于14:02Z原子改写为本会话（旧锁副本 `.coordinator.lock.prev-01a0f104`）。五域readyz只读200。

根因一：`ingest.extract_context`（CR-078）新增11处拒绝未进入Docs CI-86门禁。实读调用链：只有 `apps/suite/synwevo_suite/agent/files.py` 的文件worker经 `run_bounded` 调用它，`ServiceError.code` 写入 `agent_files.error_code`，前端 `files.ts fileErrorKey` 映射六个context_*码到 `files.messages.ts` 三语言文案（`agent-files.test.ts` 逐码断言三语言）；Docs工作台无任何路由到达。处置：门禁加一组API_ONLY决定并写明理由，而不是放宽断言或排除整文件；ingest内部 `LimitError("file size"/"text size")` 改为直接抛同一 `context_limit_exceeded`（码/状态/原句不变）。顺带发现worker自身 `context_too_large` 与业务资料选择器同码，而 `agentErrorText` 先查文件码——改worker用 `context_limit_exceeded`；`parse_cancelled` 原落到通用“无法读取个人文件”，改按“解析中断”呈现。

根因二（独立）：Suite 11个前端测试文件各自 `createServer` 共用 `apps/suite/web/node_modules/.vite`，Node并行进程下Vite依赖优化器 `deps_temp_*`→rename→`rmdir(deps)` 竞争出ENOTEMPTY；本树残留5个deps_temp_*目录为证。处置 `tests/_vite.ts`：每个测试服务器独占 `os.tmpdir()` 下mkdtemp的cacheDir、`optimizeDeps {noDiscovery:true, include:[]}`（SSR加载不需要客户端预打包）、关闭时删除自己的目录；不串行化、不删断言。

验证（本地，工作树）：Docs门禁16通过（修前第20例11条未分类，已复现）；`npm run test -w @synwevo/docs-web` 145通过；`-w @synwevo/suite-web` 两次各201通过、0 ENOTEMPTY，两次后tmpdir无残留；`pytest apps/docs/tests/test_ingest_context.py apps/suite/tests/test_agent_files.py` 98通过（PYTHONPATH指向本树，已用 `__file__` 确认）；根 `npm run typecheck`/`npm run build` 退出0；六守卫通过。导出已提交树（write-tree）：两工作区测试197+145通过；软链node_modules下tsc出现25条与本次无关文件的重复React类型错误，改用 `--preserveSymlinks` 复验见回执。未运行：完整三套pytest、infra unittest、容器验证、identity/messages/portal前端测试、浏览器。源码与日志SHA256在 `evidence/2026-10-11-ci-refusals/verification.json`。

identity `services/identity/web/tests/*.mjs` 同样共用Vite缓存但本轮未观察到失败，记为后续项，不在本次提交内改动。

## 2026-10-11 CR-079报价前来源审阅（本地+隔离浏览器）

实现：`budgetSources.ts` 只按所选引用读取（业务用 `agent.context.resolve`、文件用 `agent.file.get`，均个人上下文），核对ID、快照与ready状态，不保留正文或链接；超过5项、任一失败或响应损坏整体抛SourceReviewError。`BudgetDialog` 与offer并行读取并共用AbortSignal，列出“名称 · 类别 · 来源空间”或“名称 · 个人文件”；失败时显示 `sourcesUnavailable` 并提供重试，不出现报价按钮；意图深拷贝固定、关闭/重载/迟到回调由generation票据与alive保护。

测试：`agent-budget-sources.test.ts` 4例（只读所选、快照/删除/撤权/损坏整体拒绝、普通与重生成不读来源、超限不读）；新增 `agent-budget-dialog.test.ts` 4例，用既有编译组件夹具跑生产BudgetDialog：列表与共享信号、变化/删除/撤权阻断与重试恢复、offer失败保留原措辞、关闭中止且迟到结果不改状态。suite-web全量205通过、0 ENOTEMPTY；根typecheck/build、六守卫通过。

隔离真实服务（本树代码，Python3.12环境，127.0.0.1:33879，scratchpad数据，真实Store/认证/Docs解析/账本/报价，模型与支付为夹具传输、禁止外发）：真实文件对话框上传140字节文本并解析就绪；选择长名表单+数据表+文件后发送，对话框三语（简/英/繁）列出三项来源；每次打开恰好1 offer+2 resolve+1 file.get；390×844文档/对话框/列表项溢出均为0；Escape关闭后焦点回到打开者、草稿与已选保留；获取报价1次、确认测试预算1次→1 turn成功、1 attempt结算、1 settlement、余额0.001→0.000958，无重复quote/start。随后在Forms应用给该表单加一个字段（update_draft），再次发送：对话框显示“无法核对所选资料或其版本已变化…”、无报价按钮、无部分列表；重试重新读取仍阻止；关闭保留草稿；移除并重新选择后恢复正常。原使用旧版本的会话随即按CR-077规则隐藏历史并禁用输入。Playwright Chrome通道 `reduced_motion=reduce`：动画none/0.001s，来源在约0.8秒内显示。控制台仅登录前401与我手工无CSRF头的一次403（非产品行为）。

发现并修复：首次键盘打开（懒加载块经App路由级Suspense）会隐藏输入框，Modal捕获不到打开者，Escape后焦点落到body（探针第1次BODY/第2次TEXTAREA）；`index.tsx` 给对话框独立Suspense边界后两次均回到输入框。源码/日志指纹、截图与fixture在 `evidence/2026-10-11-agent-budget-sources/`。未声称：真实DeepSeek产物/Stripe付款、OCR/Office附件、他人撤权场景。

## 2026-10-11 CR-083取消/恢复链路审查与修复（本地）

实读engine取消/运行/执行/确认/恢复/重启/历史回放与前端重连、停止按钮；既有测试覆盖取消撤回待确认、重启对账、SSE续流、DeepSeek回放、停止前后用量、计量恢复、未知用量。成立：停止后三处检查不再派发；未知结果→`outcome_unresolved`失败、重启按op_runs对账、重生成只读；重连仅按seq续流；`_finish`终态守卫。修复：重启时`cancel_requested`回答收为cancelled（原interrupted失败并建议重试）；无运行项的取消立即返回cancelled；会话投影`turn.stopping`+Chat禁用再次停止并说明（三语）。文档docs/agent-runtime.md补“Stopping”段。

验证：`apps/suite/tests` 全量 1128 passed by progress dots (pyproject -qq hides the summary line), 0 F/E/s/x, exit 0（PYTHONPATH含本树与apps/docs/sdk/python；log sha256 6c324afde07409efaabff337e8c4a4048896cc3a258eb959ae0d5f42c80f43d5），其中新增 test_stopping_is_reported_and_a_restart_keeps_a_stopped_turn_cancelled；suite-web 205通过（log sha256 9f275813a4f53495ad23cfd2cedccffeb37c2a5c38a185e1363a326320a51f83，agent-chat新增stopping断言）；suite-web typecheck、四守卫通过。未做浏览器验收（停止反馈为既有按钮的禁用与status文本，无新布局）；未改多实例租约与队列中回答的重启语义（已在CODE_REVIEW记录）。

## 2026-10-11 e7c1f05 准确发布与生产核验

Actions38146440152 verify/deploy成功（verify 14:15:27–15:07:26Z，deploy 15:07:29–15:09:32Z）。15:10Z只读核验：current镜像e7c1f05、previous b1c8a0b、备份20261011T150904Z；四应用容器revision e7c1f05、六容器healthy；portal容器内ingest/agent.files/agent.engine/billing.ledger源码哈希与提交一致；五域readyz/根页200。回执 `evidence/2026-10-11-ci-refusals/{ci-release,production-release}.json`。CR-078附件与CI修复由此上线；不等于真实模型产物或付款验收。随后推送862b079（CR-079）与ea047ad（CR-083），由新SHA再走完整CI/CD。

## 2026-10-11 CR-084 Office 文本附件（本地+隔离浏览器）

Docs `extract_context` 新增 DOCX/XLSX 严格读取：DOCX 正文/页眉页脚/脚注尾注/批注/文本框全部文字、表格按行、链接附网址；图片→needs_ocr，嵌入对象/图表/SmartArt/公式/宏→unsupported，OLE 加密包→encrypted。XLSX 每表显示值（隐藏表标记），未缓存公式/图表/绘图/嵌入/透视/外链/批注/宏→unsupported，图片→needs_ocr。`CONTEXT_LIMITS.tables` 1→32，其余不变。Suite 前端来源标签支持 Word 部件/段落/表格行/文本框与隐藏工作表，`unsupported` 文案改为“格式或文件内容”，选择器显示 Office 说明。

测试：test_ingest_context 新增 8 例（完整 DOCX 含链接/批注、文本框只读一次、图片/对象/公式拒绝、OLE 加密与非包、XLSX 含隐藏表/日期/布尔/前导零/合并、公式/图表/图片拒绝、三类上限），与 test_agent_files 合计 106 通过；Docs refusals 门禁 16/16；agent-files 前端 23 通过；suite-web 全量 205 通过（见本节下方最终运行）；suite-web typecheck/build、四守卫通过。

隔离真实服务（127.0.0.1:33881/33883，scratchpad 数据，真实上传路由与 Docs 解析）Playwright Chrome 1280×900：能力说明列出 docx/xlsx 与 Office 注记；完整 DOCX 与工作簿解析就绪，含图 DOCX 以 OCR 文案失败，公式 XLSX 以“格式或文件内容…未保存计算结果的公式”文案失败（重建外壳后第二遍）；DOCX 预览显示“正文 第 n 段 / 正文 表格 1 第 r 行 / 页眉 1 第 1 段 / 页脚 1 第 1 段 / 批注 第 1 段”；XLSX 预览显示“表格“Main””各行与“表格“Hidden”（隐藏工作表）”；控制台仅登录前 401。截图、browser-report.json、fixture 与指纹在 `evidence/2026-10-11-agent-office-files/`。未声称 OCR/图片附件、旧二进制格式、pptx，亦未进行付费运行。

## 2026-10-11 CR-085 账户前端测试缓存隔离

services/identity/web 的 12 个 Vite SSR 测试改用 `tests/_vite.mjs`（私有 cacheDir、优化器关闭、关闭即清理，与 Suite `_vite.ts` 同契约）；清理本树残留的 5 个 `deps_temp_*`。account-web 两次全量 163 通过、0 ENOTEMPTY。

## 2026-10-11 CR-086 业务资料类型扩展（本地+隔离浏览器）

`Contexts` 改为声明式 `Kind` 契约：form/table/approval_template/approval_request/extract_template/extract_task/dataprep_recipe/dataprep_run，各走应用自己的 `.list/.get`；前缀与 kind 绑定；Extract 游标分页与 DataPrep 无搜索列表按 MAX_WALK=10 页有界走查并以 `limited` 提示。前端选择器 8 类型各有三语标签与说明，报价对话框来源显示类型。

测试：test_agent_context 新增 2 例（审批模板/请求、提取模板、数据处理方案列出/搜索/核对/一起发送，前缀错配 422；走查有界）；apps/suite/tests 全量 1130 通过（点计数，exit 0）；suite-web 206；根 typecheck/build、四守卫通过。隔离真实服务（33885，含第二成员作为审批人、真实模板/请求/提取模板/方案）Playwright：类型下拉 8 项、每类说明与对象列出、四枚来源片、报价对话框列出“名称 · 类型 · 空间”四项；控制台仅登录前 401。证据 `evidence/2026-10-11-agent-context-kinds/`。

## 2026-10-11 962b375 生产发布核验

Actions 38150240686（verify 15:11–16:10Z，deploy 16:10–16:12Z，success）回执 `evidence/2026-10-11-agent-budget-sources/ci-release.json`；16:16Z 只读核验 `production-release.json`：current 镜像 962b375、前镜像 e7c1f05、备份 20261011T161149Z，四应用容器 revision 一致、六容器 healthy，portal 内四个模块源码 sha256 与提交一致，五域 `/readyz` ready。未调用模型/支付，未改配置。

## 2026-10-11 CR-087 Admin A13 账户详情与安全时间线（本地+隔离浏览器）

`GET /api/admin/users/{uid}`（`platform.users.view`）只返回元数据：资料与验证状态、口令有无、TOTP 开关与剩余恢复码数、Passkey 名称与时间、外部身份、平台角色（含到期）、工作空间/组织成员关系（名称随对应 view 权限，否则为 null 并在界面说明）、在线会话（去掉 id）、账户桶登录退避、持有 `platform.audit.view` 时与该账户相关的最近 50 条审计事件（否则 `permitted:false`）。界面：账户行菜单「查看详情」只读对话框（`governance/AccountDetail.tsx`）。
测试：test_admin 新增 2 例（口令哈希值与 phc/secret/seed/token/hash/cookie 字段名均不出现；三次失败登录进入时间线、退避计数 3；support 无时间线但见名称；finance 403、普通账户 404、未知/已删除 404）+ 权限矩阵行；identity 套件 1549 通过、test_platform_access 29 通过；account-web 166 通过（新增 3 例）；typecheck/build 通过。
浏览器（隔离合成实例 127.0.0.1:33891，系统 Chrome）：zh-Hans/en/zh-Hant 1280×900 与 zh-Hans 390 宽均打开对话框并显示七个分节、工作空间名、外部身份、在线会话（设备/方式/时间/地址，英文为「Chrome on macOS」）、退避（等待 25 秒与可重试两种状态）、时间线中的 auth.login 被拒绝/bad_password；Escape 关闭后焦点回到行菜单按钮；support 账户查看最高管理员：时间线以说明代替、显示「权限高于你的」、名称可见。英文视图唯一中文为生成的个人空间名「… 的空间」（数据）；每个上下文一次 401 为登录前 `/api/auth/me` 探测。证据 `evidence/2026-10-11-admin-account-detail/`（browser-report.json、verification.json、5 张截图）。

## 2026-10-11 e99212d 生产发布核验

Actions 38155466408（verify 16:26–17:23Z，deploy 17:23–17:25Z，success）回执 `evidence/2026-10-11-agent-context-kinds/ci-release.json`；17:27Z 只读核验 `production-release.json`：current 镜像 e99212d、前镜像 962b375，四应用容器 revision 一致、六容器 healthy，portal 内四个模块源码 sha256 与提交一致，五域 `/readyz` ready。未调用模型/支付，未改配置。

## 2026-10-11 CR-088 图片/扫描 PDF 本地文字识别附件（本地+隔离浏览器）

Docs `extract_context(..., ocr=True)`：PNG/JPG/JPEG 与整份扫描 PDF 按页经本地引擎识别，逐行 `{page, line, recognized, confidence}`、文件级 `ocr` 汇总；每份最多 5 页；混排页/含图 DOCX 仍拒绝；无引擎 `context_ocr_unsupported`、引擎错误 `context_ocr_failed`。Suite 能力 `ocr/ocr_languages/ocr_pages`，工作进程显式请求识别，上下文项与可见来源带 `recognized`；前端能力说明、预览标记、来源行置信度、来源条标记三语言。
测试：Docs test_ingest_context 89 通过（新增 4 例，含 6 页预算在识别前拒绝、像素上限）、Docs 全量 1638 通过 2 跳过；Suite test_agent_files 20 通过（新增 1 例）、Suite 全量 1131 通过；Docs refusals 门禁 16 通过；suite-web 206 通过；suite-web typecheck/build、五守卫通过。
浏览器（隔离合成实例 127.0.0.1:33895，系统 Chrome，本机 tesseract 5.5 + 内置 chi_sim/eng）：zh-Hans/en/zh-Hant 1280×900 与 zh-Hans 390 宽均显示识别能力说明、上传 PNG 就绪、预览「识别文字（未核对）：共 3 个词…」与「第 1 页 · 识别第 1 行（置信度 89%）」及正文 SYNTHETIC FIXTURE 4821；zh-Hans 另验 JPG 就绪、两页扫描 PDF 分别识别为第 1/2 页（88%/95%）、混排 PDF 以新文案拒绝、报价对话框列出该文件；以测试预算运行一问（fixture 传输，结算 0.000042 测试单位）后，消息来源条显示「个人文件 · 识别文字（未核对）」。证据 `evidence/2026-10-11-agent-recognized-files/`（browser-report.json、verification.json、6 张截图）。

## 2026-10-11 WP8 跨空间数据流契约浏览器验收（C1/C2/C4 + 所有者改政策，隔离脚本化模型实例）

隔离合成实例（127.0.0.1:33915，真实 Store/认证/Forms/Agent 引擎/data_flow/账本/报价，模型为按关键词脚本化的测试传输，测试钱包；Alice 拥有 A，Oscar 拥有 B，Alice 仅查看 B）系统 Chrome：
C1「比较并保存」：读 A、读 B 成功；写入 A 失败 `transfer_export_denied`（卡片原句「你在来源工作空间没有导出权限…可请该空间的所有者授予导出权限」），写入 B 失败 `read_only_role`；回答如实说明，两空间表单未变。
Oscar 在 Agent 设置里把 Workspace B 的数据流政策从「仅限已有阅读者」改为「转移人可写的任何空间」（确认对话框、已明确设置、更改已保存）；Alice 获导出权后 C2「复制 B 名册到 A」：卡片等待确认，目标 Workspace A，列出来源 Workspace B（zh-Hans/en/zh-Hant 三语截图）；确认后已完成，A 的表单多出「From B roster」。
再次复制得到待确认卡片后，Oscar 改回「仅限已有阅读者」，Alice 确认 → 失败 `transfer_audience_denied`（原句「目标工作空间有不在来源工作空间的成员…」），A 未新增。C4 后效：移除 Alice 的 B 成员资格后刷新，会话 `sealed=access_changed`，依赖 B 的消息与卡片显示「内容因权限变化已隐藏」，横幅「访问权限已改变，相关内容已隐藏。请新建会话继续」，A 的记录保留。
未做：C3 同名澄清、真实模型（支出暂停）、C8 个人自动化（沿用 2026-09-30 证据）。证据 `evidence/2026-10-11-agent-cross-workspace/`（browser-report.json、verification.json、9 张截图）。夹具发现：测试模块的 SSE 片段重复同一上游 id，账本按 (environment, provider, request_id) 结算去重，第二次调用会让回合崩溃——仅夹具问题，脚本化传输改为唯一 id 后正常。

## 2026-10-11 CR-089 Admin A17 账户单项动作（本地+隔离浏览器）

三条管理路由（结束单设备登录 / 移除 Passkey / 解除外部身份）：`platform.users.manage` + 严格重认证 + 理由 + 越级检查，复用本人路径原语与锁死保护（last_factor、last_super_admin、last_sign_in_method），审计 `admin.user.revoke_session|passkey_remove|external_unlink`，本人收到 `security.change` 通知；A13 详情补回可操作 id，对话框内逐项按钮经 ActionDialog 确认。
测试：test_admin 新增 1 例通过（两台设备留一台：/api/auth/me 分别 200/401；审计含理由；重复移除 404；唯一登录方式 409；support 403；access_admin 越级 403）；权限矩阵 29 通过（新增 3 行危险路由）；account-web 167 通过（详情测试 4 例、refusals 门禁 10 例含五个新措辞）；typecheck/build、五守卫通过；identity 全量 1550 通过（19:42–19:46Z）。
浏览器（隔离合成实例 127.0.0.1:33921，系统 Chrome）：详情显示 2 个在线会话、安全密钥与外部身份各带按钮；结束一台设备后剩 1 行且两台设备 /api/auth/me 为 200/401；移除安全密钥、解除外部身份后详情不再列出；时间线显示三条 admin.user.* 事件与理由；英文视图按钮「Sign this device out」；support 账户无任何按钮并看到说明。证据 `evidence/2026-10-11-admin-account-actions/`（browser-report.json、verification.json、5 张截图）。

## 2026-10-11 CR-090 Admin A11 管理员第二因素策略（本地+隔离浏览器）

平台设置 `security.admin_second_factor`（off/required）：required 时平台管理员会话须含两类不同因素或经用户验证的 Passkey，否则 `require_platform` 回答 `step_up_required`（detail 含策略与是否已登记），`/api/auth/me` 给出 `admin_lock`；开启前至少一名可用最高管理员须已登记 TOTP 或 UV Passkey（否则 409 `no_second_factor_admin`）；平台配置页卡片与 `AdminStepUp` 按原因提示登记或用安全密钥确认/重新登录。
测试：test_platform_access 新增 1 例 + 矩阵 2 行通过（含真实 TOTP 登记与口令+验证码登录）；account-web 167 通过（refusals 门禁含新句）；typecheck/build、五守卫通过；identity+integration 全量 2903 通过（19:56–20:05Z）。
浏览器（隔离合成实例 127.0.0.1:33925，系统 Chrome）：开启被拒（卡片提示 0 人已登记，对话框显示「至少需要一名可用的最高管理员…」）→ 在账户安全页真实登记动态验证码（读取密钥用 pyotp 计算验证码，恢复码在截图前关闭）→ 卡片显示 1 人已登记与「本次登录只验证了口令」提示 → 开启成功后当前仅口令会话立即被锁（`admin_lock.reason=second_factor`，/api/admin/overview 403，管理页提示「平台要求管理员使用第二因素」）→ 退出后口令+验证码登录（amr password+totp）管理恢复 → support 角色管理员无第二因素：被锁、提示「你尚未登记安全密钥或动态验证码」并给出「前往账户安全页登记」链接，账户页步骤卡不再提供口令表单。证据 `evidence/2026-10-11-admin-second-factor/`（browser-report.json、verification.json、7 张截图）。

## 2026-10-11 CR-091 成员关系与邀请事件（本地）

`synwevo_core.member_events` 触发器把成员关系与邀请的行变化同事务写入 `job_events`（member.added/changed/removed、invitation.created/accepted/revoked，仅标识），Docs 订阅与事件流接受 `member.*`/`invitation.*`；SDK 目录与 OpenAPI 回调同步更新；api.md 与 api-coverage 记录。
测试：tests/integration/test_member_events.py 2 例（邀请→接受→改角色→移除→再邀请→撤回的完整序列与数据；feed 与 outbox 不含邮箱/令牌/空间名；另一空间凭据只见自身；订阅过滤接受 `member.*`、拒绝未知类型并在 allowed 中列出新类型）；apps/docs test_file_events + test_api_docs + 事件流/订阅相关测试通过；Python SDK 55、TypeScript SDK 48 通过；五守卫通过。未做浏览器验收（无界面变化）。

## 2026-10-11 CR-092 模板与组织模板库事件（本地）

`synwevo_core.template_events` 触发器把模板行与组织模板库的变化同事务写入 `job_events`（template.created/updated/deleted/published/released/deprecated，仅标识与版本号，发往模板所在/发布它的工作空间），Docs 订阅与事件流接受 `template.*`；SDK 目录与 OpenAPI 回调同步更新；api.md 与 api-coverage 记录。
测试：tests/integration/test_template_events.py 1 例（复用组织发布夹具：上传→新版本→发布→再新版本→后续版本→弃用→删除，7 条事件的类型、顺序与 data 逐项断言；feed 与 outbox 不含备注、文档文字或夹具名；组织内另一空间凭据只见空）；apps/docs test_file_events + test_api_docs + 事件流/订阅/成员事件测试 38 通过；Python SDK 55、TypeScript SDK 48 通过；tsc 与五守卫通过。未做浏览器验收（无界面变化）。

## 2026-10-11 CR-093 Admin A20 删除请求（本地+隔离浏览器）

后端：A13 详情新增 `deletion`（待删：申请/到期时间、是否逾期、取消链接是否仍有效；非待删：会拒绝其申请的规则），账户列表行带 `deletion_due_at`，概览 `account_deletions`（待删除/逾期/待写安全记录）；`POST /users/{uid}/deletion/cancellation-mail`（support，重发取消链接：旧链接作废、新链接到期＝删除到期、一小时 3 次间隔 1 分钟、审计不含令牌）；`POST /account-deletions/finalize`（manage，运行 Sweeper 的幂等终结）。前端：概览「注销申请」卡片与「立即处理到期删除」对话框、列表到期时间与行菜单「重发取消注销链接」、详情「注销申请」段，三语言。
测试：test_admin 新增 1 例（真实自助申请→列表/详情/概览→重认证/理由/非待删 409/一分钟内 429→重发后旧链接 410、新链接 200 且到期不变→support 200/finance 403→逾期计数→终结 1、账户 deleted、再运行 0）；权限矩阵 30 通过（新增 2 行）；邮件目录含 resend 变体；identity 全量 1552、integration 全量 1355 通过；account-web 167 通过（详情各状态、行菜单 offer、refusals 门禁）；typecheck/build、五守卫通过。
浏览器（隔离合成实例 127.0.0.1:33933，文件模式邮件无投递进程，系统 Chrome）：Alice 与 Carol 用真实自助流程申请注销（200）→ 概览卡片「待删除账户 2 / 已到期未处理 0 / 待写安全记录 0」且无按钮 → 列表状态「待删除 · 到期：2026/10/19 05:01」→ 申请后一分钟内重发被拒（对话框显示「发送过于频繁…后重试」，邮件仍 1 封）→ 等满一分钟重发成功（提示已排队；邮件 2 封；旧链接预览 410、新链接 200 且 deletion_due_at 等于申请时的值）→ 详情显示申请/到期与「取消注销的链接仍然有效」；Bob 的详情显示「将被拒绝：共享工作空间的所有权或管理权未移交」→ 英文视图行菜单「Re-send cancellation link」、详情「Deletion requested by the person on …」→ 夹具回拨 Alice 到期时间后概览「已到期未处理 1」出现按钮，确认（理由+重认证）后提示「已终结 1 个到期的注销申请。」，Alice 状态 deleted、列表不再出现 → support 为 Carol 重发成功（邮件 2 封）；回拨 Carol 后 support 概览显示逾期 1 与「你的平台角色不包含这项操作」、无按钮 → 管理员再次终结，卡片 0/0/0；审计含两次 `admin.user.deletion_cancellation_mail` 与两次 `admin.accounts.finalize_deletions`。证据 `evidence/2026-10-11-admin-account-deletions/`（browser-report.json、verification.json、11 张截图）。

## 2026-10-11 CR-094 Admin A17 推送设备（本地+隔离浏览器）

A13 详情新增 `push_subscriptions`（设备、开启时间、最近推送、失败次数、是否仍可接收；无端点），`POST /users/{uid}/push-subscriptions/revoke`（users.manage，撤销该账户全部浏览器推送订阅，幂等，审计 `admin.user.push_revoke`，本人 `security.change` `push_revoked`）。
测试：test_admin 新增 1 例（两台合成设备→详情列出且无端点→重认证→撤销 2、再次 0→详情为空、`has_active` 假→审计/通知→本人会话未受影响→support 403）；权限矩阵新增 1 行；push/notification 模块 58 通过；account-web 167 通过（推送段、按权限显示按钮、空态）；typecheck/build、五守卫通过。
浏览器（隔离合成实例 127.0.0.1:33934，系统 Chrome）：详情「推送设备（2 个）」列出 Chrome · macOS / Safari · iOS（未推送过、可接收），文本不含合成端点；「撤销全部推送订阅」经理由+重认证后提示「已撤销全部推送订阅。」，段落变为「没有开启推送的设备。」，库中 2 行 revoked_reason=admin_revoked、审计 `admin.user.push_revoke` 出现在时间线；英文视图「Push devices (0)」「No device has push enabled.」；本人通知库中 `security.change {what: push_revoked}`（脚本误用了收件箱路径得 404，改以隔离库读取）；support 看到段落但无按钮。证据 `evidence/2026-10-11-admin-push-devices/`（browser-report.json、verification.json、4 张截图）。

## 2026-10-11 CR-095 委托令牌最近使用（本地）

`oauth_tokens.last_used_at` 由 `Grants.load_access` 每令牌每分钟至多写一次；`/api/auth/connected-apps` 每条授权带 `last_used_at`，账户页「已授权的应用」显示「最近使用 {date}」或「尚未用它访问过」。
测试：test_oauth_consent（首次使用 ≥ 最近签发；分钟内第二次使用不重写；回拨 120 秒后再次使用会更新）、test_suite_machine_flow（真实 `/api/suite/v1/ops/*` 调用后授权的 `last_used_at` 非空且 ≥ 签发时间）；identity 相关 173、integration 相关 59、Docs 相关 14 通过；account-web 167/typecheck/build、五守卫通过。未做浏览器验收（一行文案）。

## 2026-10-11 CR-096 Admin A13 Agent 暂停状态（本地+隔离浏览器）

A13 详情新增 `agent_access`（暂停状态、理由、操作者、关联举报），「恢复 Agent 使用权限」从详情经理由确认后调用内容安全模块的 lift 路由；suspend/lift 审计补入理由。
测试：test_admin 新增 1 例（暂停→详情显示理由与操作者→`AgentAccess.require` 拒绝→support 可见不可恢复→恢复→详情正常→两条理由进入时间线）；相关 15 例通过；account-web 167 通过（暂停段、按权限显示按钮、正常态）；typecheck/build、五守卫通过。
浏览器（隔离合成实例 127.0.0.1:33936，系统 Chrome）：经内容安全路由暂停 Alice 后，详情显示「已暂停 Agent」徽标、理由与操作者邮箱、「恢复 Agent 使用权限」按钮；确认理由后提示「已恢复该账户的 Agent 使用权限。」，段落变为「正常：没有生效中的暂停。」，库中无生效暂停，时间线显示 suspend/lift 两条事件及各自理由；再次暂停后英文视图「Agent access / Agent suspended / Suspended since / Restore Agent access」；support 看到徽标但无按钮。证据 `evidence/2026-10-11-admin-agent-suspension/`（browser-report.json、verification.json、4 张截图）。

## 2026-10-11 CR-097 Admin A27 注册诊断（本地+隔离浏览器）

`GET /api/admin/diagnostics/registration?email=` 按地址给出七个阶段（地址校验、人机验证、频率限制、账户、验证链接、邮件、激活）各自的状态与时间；被限流的注册请求（IP 或地址窗口）现在写审计 `auth.registration.throttled`，调用方回答不变；账户页底部「注册诊断」卡片三语言。
测试：test_admin 新增 1 例（无账户→注册→待激活/已签发/已排队→第 4 次被限流并记录 scope=email→真实链接激活→support 无事件有邮件→finance 403→空/非法地址 400）；矩阵新增 1 行；注册/挑战/账户安全既有 66 例通过；account-web 170 通过（新增 3 例）；typecheck/build、五守卫通过。
浏览器（隔离合成实例 127.0.0.1:33937，开放注册、文件邮件无投递进程，系统 Chrome）：机构形式地址经公开 API 注册 4 次（均 202）→ 管理员在「注册诊断」输入地址：阶段表显示地址格式可接受 / 人机验证没有记录（未配置）/ 频率限制当前已限流（带时间）/ 账户待激活 / 验证链接已签发未使用 / 邮件已排队 / 尚未激活，事件含 auth.registration.throttled，文本不含令牌 → 从隔离发件箱取链接激活（200）后再诊断：账户正常、验证链接已使用、已激活并显示 auth.email.verified → 英文视图阶段表（Throttled now / Active / Used / Queued / Activated）→ support 看到阶段与邮件、事件段显示需要审计查看权限。带尾点地址被浏览器自带邮箱校验拦下（未发请求）。证据 `evidence/2026-10-11-admin-registration-diagnostics/`（browser-report.json、verification.json、4 张截图）。

## 2026-10-11 CR-098 Admin G19 限流状态（本地+隔离浏览器）

`GET /api/admin/security/throttles` 列出 `auth_attempts` 的全部家族（账户登录退避、来源地址窗口、会话重认证、注册/验证码/找回/初始化/安全密钥窗口、账户敏感操作、推送），地址只以哈希句柄出现，账户邮箱随 users.view；`POST /security/throttles/lift`（config.manage + 严格重认证 + 理由）解除一条并审计；账户详情登录退避段可直接解除。
测试：test_admin 新增 1 例（5 次错口令 + 注册窗口 → 列表/详情一致 → 重认证解除 → 退避清零、立即登录 → 重复 404、格式错 400、审计对象为用户、support 403）；矩阵新增 2 行；account-web 172 通过（新增 2 例；门禁补入两条拒绝句）；typecheck/build、五守卫通过。
浏览器（隔离合成实例 127.0.0.1:33940，系统 Chrome）：按服务器退避节奏输错口令 8 次后正确口令被拒（429，retry_after 59）→ 平台配置页「限流状态」卡片显示「账户登录退避 · alice… · 8 次 · 等待 59 秒 · 解除」且无原始 IP → 解除（理由+重认证）后提示「限流已解除」、行消失、Alice 立即登录 200 → 再次 3 次错口令后账户详情显示「连续失败 3 次…」与「解除登录退避」，解除后显示「没有失败的登录记录」，时间线出现两条 admin.security.throttle_lift 及理由 → 英文视图「Throttles」→ support 列表 403。证据 `evidence/2026-10-11-admin-throttles/`（browser-report.json、verification.json、5 张截图）。

## 2026-10-12 CR-099 Admin A49 有效访问解释（本地+隔离浏览器）

`GET /api/admin/users/{uid}/access?workspace_id=&app_id=` 按七道闸门解释某人能否在某空间打开某应用，裁决来自真实 `Apps.check_access` 并标明一致性；A13 详情工作空间行「解释访问」打开对话框（应用选择、裁决、闸门表）。
测试：test_admin 新增 1 例（全通过→撤销资格→非成员→停用空间应用→冻结空间，各闸门与裁决一致且读取不改变状态；auditor 可读、support 403；缺参 400、未知 404）；矩阵新增 1 行；account-web 173 通过（新增 1 例；门禁补入两条拒绝句）；typecheck/build、五守卫通过。
浏览器（隔离合成实例 127.0.0.1:33941，系统 Chrome）：Alice 详情 → 共享空间行「解释访问」→ 「可以打开：Synwevo Docs（…）」且七道闸门全部通过（成员资格「编辑者 · 可导出」、使用资格「无单独记录 · 成员即可使用」）→ 经真实冻结路由（理由+重认证）冻结该空间后再解释：空间状态「未通过 · 已冻结」、裁决「不能打开：…」→ 解冻 → 英文视图「Can open: …」与七道闸门英文 → support 详情无按钮、API 403。证据 `evidence/2026-10-12-admin-effective-access/`（browser-report.json、verification.json、3 张截图）。

## 2026-10-12 CR-100 Admin A32 第二因素普及（本地+隔离浏览器）

概览 `second_factor` 聚合（正常账户、任一第二因素、动态验证码、安全密钥/其中可验证身份、恢复码已用尽、可用最高管理员总数/已登记）与概览卡片。
测试：test_admin 概览断言（全零基线）+ 新增 1 例（两人真实登记动态验证码、一人安全密钥行：数字随之变化；恢复码用尽计数；响应不含密钥）；account-web 173 通过；typecheck/build、五守卫通过。
浏览器（隔离合成实例 127.0.0.1:33942，系统 Chrome）：卡片基线 5 个正常账户、全部 0 → 成员经真实自助路径登记动态验证码（200）→ 卡片「已登记任一第二因素 1 / 动态验证码 1」，文本不含密钥 → 英文视图「Second-factor adoption … With any second factor 1」。证据 `evidence/2026-10-12-admin-second-factor-adoption/`（browser-report.json、verification.json、3 张截图）。

## 2026-10-12 CR-101 API traceparent 进调用记录（本地）

网关解析 W3C `traceparent`，trace-id 记入 `api_platform_calls.trace_id`（追加列），控制台 `/calls?trace_id=` 可筛选并在行内显示；非法值忽略。
测试：tests/integration/test_api_platform.py 新增 1 例（合法 traceparent → 记录 trace_id 并可按大写查询；garbage/全零 trace/版本 ff/全零 parent 四种非法值 → 请求 200 且 trace_id 为空；未知 trace 查询为空）；integration 相关子集 355、Docs API 子集 71 通过；account-web 173/typecheck/build、五守卫通过。未做浏览器验收。

## 2026-10-12 CR-102 Admin A11 第二因素宽限期（本地+隔离浏览器）

要求第二因素时可设 0–30 天宽限期：期内未登记/会话不强的管理员只在账户页看到到期提醒并可继续管理，期满锁定；策略卡显示宽限期至何时；`/api/auth/me` `admin_grace`、`/api/admin/session` `step_up.grace_until`。
测试：A11 测试扩展（40 天 → 400；7 天 → required 且 grace_until 在 6–7 天间；仅口令会话仍 200；`admin_grace` 对未登记者给出、对强会话为空；期满回拨 → 403 且 admin_grace 为空；off 清除键；审计序列 required/required/off）；identity 相关子集 975、integration 相关子集 182 通过；根 typecheck、account-web 173/build、五守卫通过。
浏览器（隔离合成实例 127.0.0.1:33944，系统 Chrome）：最高管理员真实登记动态验证码 → 平台配置页选择「要求管理员使用第二因素」、宽限期 7 天、保存（理由+重认证）→ 策略行「宽限期至 2026/10/19 …：期内只提醒，不锁定」，概览仍 200，`admin_grace` 返回 → 其账户页显示「平台将从 … 起要求管理员使用第二因素」与已登记文案 → support（未登记）`/api/admin/users` 200、账户页提醒含「前往账户安全页登记」按钮、英文提醒 → 夹具回拨到期后 `/api/admin/users` 403、`admin_lock.reason=second_factor`。证据 `evidence/2026-10-12-admin-second-factor-grace/`（browser-report.json、verification.json、4 张截图）。

## 2026-10-12 CR-103 Admin G24 管理操作目录（本地+隔离浏览器）

`admin_actions.py` 列出全部 47 个 `admin.*` 动作（9 个范围、三语言），`GET /api/admin/audit/actions` 提供给审计页；源码扫描测试保证目录与代码一致。
测试：test_admin 新增 1 例（源码字面量集合 == 目录；首次运行即发现子包里的 `admin.model_provider.closed` 未入目录，已补）；矩阵新增 1 行；account-web 173 通过；typecheck/build、五守卫通过。
浏览器（隔离合成实例 127.0.0.1:33946，系统 Chrome）：经真实冻结/解冻路由产生审计后，审计页筛选下拉含 9 个范围分组共 56 个选项，选择「冻结工作空间」只剩 1 行且行内显示「冻结工作空间 / admin.workspace.freeze」；英文视图「Freeze a workspace」。证据 `evidence/2026-10-12-admin-action-catalogue/`（browser-report.json、verification.json、2 张截图）。

## 2026-10-12 CR-104 Admin A5 权限模拟（本地+隔离浏览器）

`GET /api/admin/users/{uid}/platform-access/simulate` 算出一次假想授予/撤销前后的角色与权限、新增/失去项与调用者可行性；权限对话框「模拟一次变化」。
测试：test_platform_access 新增 1 例（授予审计查看：新增其全部权限、可行；对自己授予/撤销最高管理员：self_assignment；撤销未持有角色：not_held；邮箱未验证：不计入、无新增；审计员可读但不可行 platform_permission_denied；授予表未变）；矩阵新增 1 行；account-web 175 通过（新增 2 例）；typecheck/build、五守卫通过。
浏览器（隔离合成实例 127.0.0.1:33947，系统 Chrome）：Alice 的平台权限对话框 → 「模拟一次变化」授予「审计查看」→ 「你现在可以执行这一变化」与九项将新增权限、不会失去权限 → 撤销「支持」→ 「该账户并未持有这个角色」且无变化 → 英文视图「If “Audit viewer” were granted · You could make this change now.」→ 授予表行数前后相同（2）。证据 `evidence/2026-10-12-admin-permission-simulation/`（browser-report.json、verification.json、2 张截图）。

## 2026-10-12 CR-105 Admin G24 写入与审计同事务（本地）

`account-deletions/finalize` 的扫描与审计改为同一事务；新增故障注入测试：审计行写入失败时 8 条管理写入路由各自返回 ≥500、状态原样、无审计行、故障原文不外泄且带 request_id。源码扫描：直接写审计的 11 条路由与 claim/unclaim 均在事务内。
测试：test_admin 相关 9 例通过；identity `-k admin` 子集 666 通过；五守卫通过。无界面变化，未做浏览器验收。

## 2026-10-12 CR-106 WP1 Forms 桌面宽屏与英文/繁体走查（本地+隔离浏览器）

隔离合成实例 127.0.0.1:33951（role=all，SUITE_APPS=forms,tables，文件邮件），系统 Chrome，视口 1440×900。以空间所有者经真实操作 API 建表单（4 个字段）→发布版本 1→三份填写请求（每语言一份，一次性策略）；三种语言分别以成员走表单库/编辑器/填写请求/提交记录四页，并以匿名浏览器打开真实链接填写提交，回执页显示表单自定义确认语与回执编号，提交随后出现在成员的提交记录。
检查项：表单标题可见（标题或编辑器标题输入框）、三个分区标签为该语言、`scrollWidth` 不超视口、英文页在数据之外无汉字（个人空间名「… 的空间」是账户创建时按 zh-Hans 生成的数据，语言菜单项为各语言自称）。控制台仅登录页匿名探测 `/api/auth/me` 的一次 401，属预期。
走查发现 3 处并修正后重跑取证：英文表单库标题「Form」→「Forms」（源文改用「表单库」；Forms 目录「表单」改为单数「Form」与其他目录一致）；「1 submissions」→「1 submission」（拆两条复数消息；`packages/ui` 翻译器无 `count` 时按唯一数字参数选形，新增测试）；空间切换器无组织名时不再以「 · 」开头。
验证：packages/ui 20、auth-client 41、docs-web 145、messages-web 25、suite-web 206、account-web 175 全部通过；根 typecheck/build 通过；五守卫通过；Python 套件未跑（无 Python 改动）。证据：evidence/2026-10-12-forms-wide-locales/（15 张截图、browser-report.json、verification.json）。

## 2026-10-12 CR-107 Admin G24 统一前后值（本地+隔离浏览器）

`synwevo_core.audit_change.change(before, after)` 产出审计 meta 的三个固定键：同键快照 `before`/`after`（一侧缺的键记为空）与差异键列表 `changed`（空操作为空列表，照实记录）。应用于账户服务的注册策略、第二因素策略（含宽限期截止）、解除限流、冻结/解冻、认领/撤回历史数据、终结删除、撤销全部/单设备会话、移除安全密钥、解除外部身份、撤销推送、Agent 暂停/恢复，以及核心的平台角色授予/撤销（含系统路径、运维角色清除）与组织状态变更。审计页新增「变化」列按 `changed` 逐键显示「键 前 → 后」，没有记录的动作显示「—」。
测试：`test_admin.py` 新增 2 例——纯函数形状；经真实路由做 11 种变更后逐动作断言三键结构与期望前后值（过程中发现一次真实空操作：刚申请注销的账户再撤销会话时 `changed` 为空，测试改为在会话存活时撤销）；`test_platform_access.py` 3 处既有断言改为新结构；account-web 新增 `admin-audit-changes.test.mjs`（变化行、旧标量形状不视为变化记录、结构值以 JSON 打印）。相关 Python 套件 305 passed, 0 failed（进度行计数；-q 汇总行未输出）；account-web typecheck + 177 通过；五守卫通过。
浏览器（隔离合成实例 127.0.0.1:33953，系统 Chrome，1440×900）：最高管理员经 `/api/auth/reauth`（scope=admin）后以真实路由冻结→解冻合成共享空间、两次切换注册策略；审计页（简体/English）表头含「变化」/「Change」，按动作筛选后各行显示 `frozen false → true`、`frozen true → false`、`mode invite → open`、`mode open → invite`；英文页数据之外无中文。证据：evidence/2026-10-12-admin-audit-changes/（3 张截图、browser-report.json、verification.json）。

## 2026-10-12 CR-108 推送前对抗式审查与修复（本地+隔离浏览器）

审查：workflow `wf_766e2d36`，64 个只读代理（7 名审查者 + 每条发现 3 名反驳者），对 `origin/main..HEAD` 24 个提交；19 条原始发现，14 条成立（4 中 10 低），5 条驳回（结论见 CODE_REVIEW CR-108 表）。全部成立项已修复。
修复验证：account-web typecheck + 178 项（新增访问解释翻译、宽限期保存、分隔符、模拟拒绝句、审计变化列等 SSR 测试）；六个前端工作区共 613 项通过；根 typecheck/build 通过（五个外壳）；五守卫通过；`test_platform_access.py` 48 项、`test_external_identity.py` 等 61 项、`test_admin.py` 全文件（含新增 5 项与故障注入 8 路由）通过；`test_admin_operations.py`/`test_admin_settings.py` 新增 `changed` 断言通过；Docs 契约同步测试通过（`openapi.yaml` 由 CLI 重新生成）。推送前清单：仓库根 `pytest` 全量 5791 通过、2 跳过（37 分钟）；`SYNWEVO_SHEET_CLIENTS=required` sheet 客户端测试 98 通过；infra/production unittest 12 通过；SDK Python 55、TypeScript 48 通过；根 typecheck/build（五个外壳）与六个工作区测试 613 通过；五守卫通过。
浏览器（隔离合成实例 127.0.0.1:33954，重建后的账户外壳）：重跑 2026-10-12-admin-audit-changes 的证明——冻结/解冻/注册策略变更仍按键显示前后值，且英文审计页按 `admin.workspace.freeze` 筛选只列冻结行（精确匹配，C4）。

## 2026-10-12 CR-109 G26 WP1 一个审计写入口（本地）

`synwevo_core.auth.audit_event` 成为平台与应用共用的唯一审计写入口（D7）：按 `authority.describe*` 描述符映射操作者（人 / 委托客户端后的人 / 机器凭据 / 系统），网关把当前主体放进 `context.principal()`，服务层写行时不必拿着 request 也能说清是谁、是哪次请求；`Platform.audit` 与 `Capabilities._audit` 都委托给它，行内容不变（meta 统一为规范 JSON）。
测试：`tests/integration/test_audit_actor_mapping.py` 3 例（五种身份映射与无身份回落、上下文主体/请求 ID 与显式参数的优先级、与写入同事务回滚）；连同账户服务 test_admin/test_platform_access/test_admin_unclaim、集成 test_api_platform/test_credentials_keys、Docs 的 governance/rescan/scan_recovery、Suite 的 capabilities/machine_api/machine_webhooks/agent 系列共 527 通过；五守卫通过。全量：仓库根 `pytest` 全量 5794 通过、2 跳过（24 分钟，含新增 3 例）。

## 2026-10-12 CR-110 G26 WP2 Docs webhook 审计（本地）

八种订阅/投递操作各自在写入事务内留一行审计（`docs.webhook.*`），机器调用方记为 `api:<key id>`，meta 只含主机、事件、版本、投递与订阅标识；调度器放弃目标写系统行 `webhook-dispatcher`。测试：`test_webhook_audit.py` 3 例（八操作各一行且不含密钥/路径、更新行的 `changed`、重放不加行、审计失败回滚暂停与删除、自动停用系统行）；连同 redelivery/hardening/subscriptions/contract/CLI 测试 61 通过；五守卫通过。Docs 全套件：`apps/docs/tests` 全套件 + 审计映射测试 1644 通过、2 跳过（7 分钟）。

## 2026-10-12 CR-111 G26 WP3 共享模板/评论/复制申请审计（本地）

组织共享模板五种操作与评论五种操作改由服务在写入事务内审计（两种调用面各一行，正文与名称不入 meta），复制申请/自批/撤回的路由审计并入事务。测试：`test_audit_sharing_templates.py`（五操作各一行、不含名称与备注文本；审计失败回滚 use 的模板复制与 use_count）、`test_audit_sharing_comments.py`（五操作各一行、管理员代删标记、正文不入记录；审计失败回滚删除）共 4 例通过；既有 99 项相关测试通过；五守卫通过。全套件：Docs 与集成两套件 3004 通过、2 跳过（12:40–12:57Z，日志 scratchpad/a13/g26wp3-suites.log，未入库）；完整根套件在 WP4 推送前再跑一次。

## 2026-10-12 CR-112 G26 WP4 Docs 项目与来源审计（本地）

项目与来源的 11 种写操作改由服务在写入事务内审计（浏览器与 v1 两面各一行，前后值只含 template_id/template_version/archived_at/settings_rev/代表文件/顺序/版本/状态等标识符），`_project_changed` 改为提交后触发。测试：`tests/integration/test_audit_coverage_projects.py`（4 例：11 条路由各一行并校验 `authz.ROUTE_POLICY` 分类、步骤导航零行、审计失败回滚且无活动行、查看者拒绝零行）、`apps/docs/tests/test_audit_projects_v1.py`（4 例：10 条 v1 路由各一行、幂等重放不加行、陈旧 PATCH 无行、OCR 行无文字）通过；既有 135 项相关测试通过（`test_project_collab`、`test_services_views`、`test_api_source_operations`、`test_file_events`、`test_refusal_details`、`test_gate_roles`、`test_project_copy`）；五守卫 + `app_icon_assets --check` 通过（13:01Z）。完整根套件在 WP5 后、推送前跑一次。

## 2026-10-12 CR-113 G26 WP5 模板/版本/映射/定义审计（本地）

模板上传/内置创建/每个版本/恢复/撤销重做/改名/删除/复制、映射保存/撤销/重做、字段定义导入与项目定义导入改由服务在写入事务内审计；`audit_event` 回落到 `context.actor()`；共享模板动作改名 `docs.shared_template.*`。测试：`tests/integration/test_audit_coverage_templates.py`（4 例：15 条路由各一行、审计失败回滚版本保存、使用中删除 409 零行、查看者零行）；`apps/docs/tests/test_audit_templates_v1.py`（5 例：v1 上传/内置创建/保存/两个字段定义导入只留一行且无中间版本行/字段改删/恢复/映射保存两次/定义导入四行同 request_id/删除各恰好一行，机器操作者 api:<key>；幂等上传重放不加行；save_live_sheet 在 set_actor 下归因到人；审计失败回滚字段导入）；既有 325 项相关测试通过（13:03Z，含 WP4 覆盖测试）；五守卫 + 图标检查通过（16:05Z）。

## 2026-10-12 CR-114 G26 WP6 任务审计（本地）

任务 create/cancel/retry 在插入分支内审计，重放与无变化取消零行。测试：`apps/docs/tests/test_audit_jobs.py`（5 例，17 项含既有幂等/授权测试通过）；既有 28 项任务授权/幂等/重试/验收/链接测试通过（13:08Z）。

## 2026-10-12 WP4–WP6 推送前完整根套件（本地）

6b0d550d 树上 `pytest`（根，三套件）：5821 通过、2 失败、2 跳过（16:21–16:45Z，日志 scratchpad/a13/g26wp456-full.log，未入库）。两例失败均为 `test_idempotency_durability.py::test_source_parser_waits_for_creator_commit_or_rollback`：WP4 把来源解析/扫描改为 `after_commit` 启动，而既有设计是解析器自行等待创建者提交或回滚（外层幂等事务下 after_commit 不会在回滚时运行、也不会在提交前启动）。cb157e83 恢复原语义（事务块结束后立即提交解析），定向重跑 111 项（耐久性、文件事件、来源操作、WP4 两面覆盖）通过；推送前再跑 Docs 套件：cb157e83 树上 `apps/docs/tests` 1655 通过、2 跳过（16:45–16:52Z）。前端未改动，typecheck/build/npm test 沿用 2066a035 推送前的结果。。

## 2026-10-12 CR-115 G26 WP7 上传/签名链接/回归用例审计（本地）

测试：`apps/docs/tests/test_audit_uploads_links.py`（5 例：上传 create/complete/consume/abort（请求与隔离判定两种原因）各一行、重放与重复零行、消费行与 source.add 行同 request_id；artifact/archive 链接各一行且无令牌/URL、重放零行；回归用例行先于目录（审计失败则不建目录）、删除行；审计失败回滚上传完成并可重试）；既有 92 项（上传契约/重放/耐久性、链接契约、回归用例与隔离、幂等、WP4 覆盖）通过（16:54Z，耐久性测试崩溃点按同事务语义调整后 16 项通过）。

## 2026-10-12 CR-116 G26 WP8 Suite 能力中心审计（本地）

测试：`apps/suite/tests/test_audit_capability_path.py`（5 例：入口处 scope/无 scope/Agent 拒绝各一条 denied 行、ui 拒绝零行；ui 调用为人、委托 OAuth 为人 + delegation + ip/request_id、服务账户与密钥为 machine；事务型/两阶段失败各恰好一条 error 行且无 ok 行；机器拒绝一分钟一行、ui 拒绝零行、HTTP 路径带 delegation；异步受理行 phase=accepted、完成行 phase=completion（成功/失败）、flow 委托随行）；定向 85 项通过（16:58Z）；Suite 全目录 `apps/suite/tests`：1131 通过（17:05Z，含不可读主体完成行修复）；拒绝行扩展到入口处的 scope/自动化/Agent 拒绝后再跑 1135 通过（17:11–17:14Z，含子代理的 4 例；第 5 例随后单独通过）。

## 2026-10-12 CR-117 G26 WP9 Suite 旁路路径审计（本地）

测试：`apps/suite/tests/test_audit_bypass_paths.py`（5 例：十种字节搬运路由两面各一行（凭据路径带 delegation、上传重放零行、提取导出会话一行/凭据路径只有操作行）；公开填表草稿零行、上传/放弃/提交/补交匿名行（pub: 哈希前缀）、专属链接 recipient:、登录填写人为人；到期与审批提醒归因到引擎；自动化触发系统行、手动启动零行；审计失败回滚公开提交）；Suite 全目录 1136 通过（17:28–17:31Z，含 WP9；覆盖测试随后 5 例通过）。

## 2026-10-12 CR-118 G26 WP10 Webhook 操作审计与签名元数据（本地）

测试：`apps/suite/tests/test_audit_webhook_operations.py`（Suite 3 例：ping/redeliver 各一行且对象为投递记录、429/409 零行；410 Gone 触发的停用一行（原因/未送位置/丢弃数/状态前后）、陈旧版本零行；每次尝试记 12 位密钥指纹与时间、公开视图不含、轮换后指纹变化；Docs 1 例（test_webhook_delivery.py）同样覆盖指纹/时间/视图/轮换）；两应用 webhook 相关 132 + 122 项（含迁移测试）通过（17:31–17:35Z）。

## 2026-10-12 CR-119 G26 WP11 审计页应用动作目录（本地）

测试：账户服务目录 3 项 + 集成比对 2 项 + 两应用源码扫描 3 项通过（17:36Z）；账户前端 typecheck 与 node 测试通过；根 typecheck/build 通过，六个前端工作区 615 项 node 测试通过（17:37Z）。

推送前完整根套件（WP7–WP11 树 221a59b6，三个套件一次运行）：**5848 通过、0 失败、2 跳过**（17:45Z–18:07Z，`scratchpad/a13/g26wp7to11-full.log`）；五守卫 + 图标检查通过（17:45Z）。

## 2026-10-12 CR-120 A37 平台受控所有权恢复（本地）

测试：`services/identity/tests/test_admin_ownership_recovery.py` 34 项 + 相关 161 项（test_admin_ownership / test_admin / test_org_roles / test_admin_governance / test_org_audit / test_organizations）= 195 通过（后端子代理，18:05Z 前后）；账户前端 `npm test` 183 项通过（含新 `admin-ownership.test.mjs` 5 项与 refusals 放置表）、根 typecheck/build 通过（18:15Z）；五守卫 + 图标检查通过（18:16Z）。
账户服务 + 集成套件（`services/identity/tests tests/integration`，18:14Z–18:23Z）：2999 通过 / 1 失败——`test_platform_access.py::test_every_admin_route_is_in_the_matrix` 要求新路由写明权限，已把三条路由加入矩阵（清单 `platform.workspaces.view`、两条交接各自对象的 manage 权限 + 严格重认证），该文件重跑 48 项通过（18:25Z）。

对抗性审查（只读子代理，18:14Z–18:27Z，8 个探针）：SQL 资格与 D11 一致、事务/竞态/审计同事务、授权与步进、错误契约、测试非同义反复、前端门控与样式类、目录均无缺陷；发现并已修正——(1) 第二因素判定改为与 A11 同一的 `PlatformAccess.has_second_factor`（TOTP 或可验证用户的安全密钥，原实现仅 TOTP）；(2) 候选人列表无界 N+1 → 每个孤儿一条查询（`platform_access.SECOND_FACTOR_EXISTS` / `LIVE_GRANT_EXISTS` 片段 + 一致性测试）、封顶 100 位、`candidates_total`；(3) 补工作空间路径审计失败回滚测试；(4) 组织路由前置 `not_recoverable` 检查与工作空间路由同序；(6) 结果提示二次翻译、审计页组织范围快捷项。修正后定向 225 项通过（新文件 38 项，18:33Z）；账户前端 183 项、根 typecheck/build、六个前端工作区 620 项 node 测试、五守卫 + 图标检查通过（18:37Z）。完整根套件（最终树，三个套件一次运行，18:37Z–19:00Z，`scratchpad/a13/a37-full.log`）：**5886 通过、0 失败、2 跳过**。

## 2026-10-12 CR-121 A35 组织生命周期的平台作用域路径（本地）

测试：`services/identity/tests/test_admin_organization_lifecycle.py` 30 项 + 相关（test_admin_governance / test_platform_access / test_admin / test_organizations / test_org_audit / test_org_roles / test_admin_operations）= 231 通过；受影响的 test_admin_write_races / test_admin_ownership_recovery / test_admin_ownership 96 通过（后端子代理，19:2xZ）。账户前端 `npm test` 190 项通过（含新 `admin-organization-lifecycle.test.mjs` 7 项与 refusals 放置表）；根 typecheck/build、六个前端工作区 627 项 node 测试、五守卫 + 图标检查通过（19:28Z）。
账户服务 + 集成套件（`services/identity/tests tests/integration`，2026-10-12 19:28Z–19:37Z）：**3034 通过、0 失败**。第一次对抗性审查子代理因模型额度中断（未产出结论），2026-10-13 00:30Z 用当前模型重新发起；完整根套件同时开始。

对抗性审查（只读子代理，2026-10-13 00:30Z–00:42Z，8 个探针）：重构保真（`transition` 顺序、错误码、返回值、审计 meta 字节一致）、写事务内重读与摘要、并发与重放、授权与步进、签名计划绑定、影响计数谓词、错误契约均无缺陷。发现并已修正：(1) 中——删除前已冻结的组织恢复后仍为冻结，但对话框写“成员恢复访问”，测试固化了错误文案 → 按 `result_status` 选择含义并加“仍为冻结”提示，测试改为两种情形；(2) 中低——组织自己的审计页把平台的 `admin.*` 记录（每次成功 + 每次被拒预览及操作者理由）以原始代码显示 → 组织日志查询排除 `admin.%`，新增租户视角测试；(3) 低——`by: platform` 未在租户页显示 → 显示“由平台管理执行”（三语言）；(4) 低——计划摘要只比计数，成员一进一出不失效 → 绑定成员 ID/角色与空间 ID，新增换人测试；(5) 低——平台审计与操作记录在安全日志之后写 → 平台审计改在状态机之前写（日志仍在核心最后），操作回执说明为本事务独占的插入；(6) 低——`platform_access.py` 过时说明已改；(7) 测试——补安全日志失败 503 全回滚、平台审计失败全回滚，修正一处空集合恒真断言与一处依赖 sleep 的计时测试。未改：`labels.ts` 的 `no_organization` 兜底文案（服务端句子已注册，优先显示）、`messages.ts` 中三条已不用的旧文案。
修正后：受影响账户服务测试 241 项中 239 通过 2 失败（一处为有意改变的记录顺序断言、一处测试误用 `monkeypatch.undo()` 撤销了夹具环境），修正后生命周期/治理/组织审计 60 项通过；账户前端 190 项、五守卫、根 typecheck/build 通过（00:47Z）。
完整根套件（修正前的树，00:30Z–00:59Z）：**5916 通过、0 失败、2 跳过**；修正后的树：账户服务 + 集成套件 **3038 通过、0 失败**（00:59Z–01:09Z），六个前端工作区 627 项 node 测试、五守卫、图标与 Logo 检查、根 typecheck/build 通过。修正只涉及账户服务与核心的组织审计读取，其余两套件未受影响。

## 2026-10-13 CR-122 A18 批量停用与批量撤销会话（本地）

测试：`services/identity/tests/test_admin_bulk_accounts.py` 14 项 + 相关（test_admin_operations / test_admin_ownership / test_platform_access / test_admin / test_admin_governance / test_admin_write_races）= 229 通过，跨模块 `tests/integration/test_admin_user_operations.py` 与 `test_admin_recovery_safety.py` 7 项通过（后端子代理，01:2xZ）。账户前端 `npm test` 199 项通过（含新 `admin-bulk-accounts.test.mjs` 9 项）；六个前端工作区 636 项 node 测试、五守卫、图标与 Logo 检查、根 typecheck/build 通过（01:29Z）。
账户服务 + 集成套件（01:28Z–01:38Z）：**3052 通过、0 失败**；完整根套件 01:39Z 开始，对抗性审查进行中。

对抗性审查（只读子代理，01:28Z–01:40Z，9 个探针）：单账户规则逐项复核、事务与崩溃续做、授权与步进、签名计划、限制、测试、前端均无绕过。发现并已修正：(1) 中——批量停用时前一个账户的停用改变后一个共同拥有者/管理员的唯一拥有者摘要，使其被报 `admin_preview_stale`（k 个共同管理员需 k 轮），且预览把最后两名拥有者都标为可处理 → 停用预览在写事务的 SAVEPOINT 内逐个模拟前序账户 `users.status='suspended'`，签名前回滚；最后两名拥有者的第二个在预览即为 `ownership_required`；(2) 低中——幂等键直到最后一个事务才占用，同键并发或超时重试时两路各自执行 → 首个事务即写入“运行中”批次行，同哈希续做、异哈希冲突且不执行任何账户，预览过期后的续做把剩余账户报过期并关闭批次；(3) 中断批次的错误行带批次号；(4) 账户 ID 限 `[A-Za-z0-9_-]{1,128}`（孤立代理项曾致 500）；(5) 批次中途失权保留被拒审计行；(6) 补撤销会话批次的崩溃续做测试；(7) 两种预览都要求 `platform.users.view`；(8) 前端：搜索框与筛选中的 Escape 不再清空跨页选择。修正后：定向 219 项通过（批量测试 14 → 24 项；临时关闭模拟时四项共同拥有者测试失败、恢复后通过），六个前端工作区 636 项、五守卫、根 typecheck/build 通过（01:47Z）。
修正后的树：账户服务 + 集成套件 **3062 通过、0 失败**（01:47Z–01:57Z）。
完整根套件（01:38Z–02:03Z，审查修正前开始；修正只涉及账户服务与其前端，已由上一行覆盖）：**5934 通过、0 失败、2 跳过**。

## 2026-10-13 CR-123 A26 第二因素受控重置（本地）

测试：`services/identity/tests/test_second_factor_reset.py` 12 项 + 相关（test_platform_access / test_admin / test_admin_operations / test_admin_bulk_accounts / test_mfa / test_self_service / test_account_link_privacy / test_mail_catalogue）= 259 通过；`tests/integration/test_security_journal.py`、`test_blob_references.py`、`test_account_restore.py` 51 通过；间接相关 136 通过（后端子代理，02:2xZ）。账户前端 `npm test` 210 项通过（含新 11 项）；六个前端工作区 647 项 node 测试、五守卫、图标与 Logo 检查、根 typecheck/build 通过（02:32Z）。完整根套件（审查修正前的树，02:32Z–02:57Z）：**5958 通过、0 失败、2 跳过**。

对抗性安全审查（只读子代理，02:32Z–02:44Z）：延迟无法被缩短或跳过、令牌不外泄、公共路由与免登录白名单、完成步骤、重构、备份恢复与边界均无缺陷。发现并交后端修正：(1) 高——完成时不核对通知邮件是否送达；(2) 高/中——完成时不复核发起人仍持有 `platform.users.recover`；(3) 中——邮箱变更（请求前 7 天内或期间）未作废；(4) 中——移除的安全密钥未写安全日志，恢复备份会复活；(5) 低中——完成时不核对因素仍丢失（期间用过 TOTP/恢复码即作废）；(6) 停用账户未作废待生效重置；(7) 未验证邮箱；(8) 作废未通知；(9) 清扫各任务未隔离异常；(10) 测试缺口。修正与复验结果追加于下。
修正（后端子代理，02:44Z–02:58Z）：完成须通知邮件已送达且送达满 72 小时，送达失败或到期后 24 小时仍未送达即作废；发起人失权作废；邮箱 7 天内变更拒绝申请、期间变更作废；移除的安全密钥逐个写 `passkey_removed` 日志；期间第二因素被使用或变化作废、到期已无可重置项作废；停用账户同事务作废；未验证邮箱拒绝/作废；作废通知本人（非邮箱类原因）与申请人；恢复报告列出作废项；清扫各任务独立捕获异常。定向 294 项通过（重置测试 12 → 31 项，三处临时变异各使对应测试失败）。前端：12 种作废原因与 2 个新拒绝码三语言标签、待生效重置显示通知邮件状态与实际生效时间、免登录页面以服务器为准判断可否取消；账户前端 210 项、六个前端工作区 647 项、五守卫、图标与 Logo、根 typecheck/build 通过（03:00Z）。
修正后的树：账户服务 + 集成 + Docs 快照套件 **3125 通过、0 失败**（03:00Z–03:11Z）；因修正涉及核心 schema 与通知，再跑完整根套件（03:12Z–03:38Z）：**5978 通过、0 失败、2 跳过**。

## 2026-10-13 CR-124 A22 应用身份排除出人员治理（本地）

测试：`services/identity/tests/test_admin_application_identities.py` 4 项通过（03:40Z）；账户前端 210 项、五守卫、根 typecheck/build 通过（03:4xZ）。账户服务 + 集成套件（03:40Z–03:52Z）：**3100 通过、0 失败**。改动只涉及账户服务管理路由与账户前端（完整根套件已于 03:38Z 在其前一提交上通过）。

## 2026-10-13 CR-125 A47 平台邀请监管（本地）

测试：`services/identity/tests/test_admin_invitations.py` 12 项 + test_platform_access / test_admin / test_members / test_invites / test_member_import / test_org_audit = 195 通过，邮件标签页 test_admin_governance 22 通过（后端子代理，04:0xZ；另以临时破坏脱敏与错误归类验证测试会失败）。账户前端 `npm test` 221 项通过（含新 11 项）；六个前端工作区 658 项 node 测试、五守卫、图标与 Logo、根 typecheck/build 通过（04:11Z）。账户服务 + 集成套件（04:10Z–04:22Z）：**3112 通过、0 失败**；

对抗性审查（只读子代理，04:11Z–04:23Z）：A47 端点本身无缺陷（令牌/原始错误不外泄、与接受并发安全、授权与参数化正确）。发现并已修正：(1) 中——脱敏形同虚设：持 `workspaces.view` 的角色都持 `mail.view`，邮件标签页返回邀请邮件完整收件地址且可子串搜索 → 邀请邮件在邮件标签页与注册诊断中同样脱敏、只能按完整地址或 `@域名` 搜索，其他邮件种类不变；(2) 中低——列表在全局写锁内做两次全表扫描 → 改为单一延迟读事务、仅待接受行查邮件、新增 `ix_inv_created` 索引（以另一连接持写事务的测试证明不再等待）；(3) 批量中途异常整批 500 → 逐项 `admin_operation_failed`、其余 `batch_stopped`、批次行在 finally 写入；(5) 个人空间看不到平台撤销 → 租户邀请列表新增 `revoked_by`，前端标“由平台管理撤销”；(6) 撤销理由对组织审计读者可见 → 对话框说明；(7) 零值时间戳导致无限递归 → `_state` 用 `is not None`、不递归；(10) `invite_not_pending` 兜底文案；(11) `has_account` 仅对持 `users.view` 者返回。接受：(4) 安全日志先于审计写入（受限意图）；(8) 逐项被拒审计沿用先例。修正后定向 206 项（邀请测试 20 项）+ 82 项通过；账户前端 221 项、五守卫、根 typecheck/build 通过（04:36Z）。因核心新增索引，再跑完整根套件（04:35Z–05:00Z）：**6004 通过、0 失败、2 跳过**。

## 2026-10-13 CR-126 B1/B2 应用目录的平台管理（本地）

测试：`services/identity/tests/test_admin_apps.py` 21 项 + test_platform_access / test_admin / test_cli_governance / it test_security_journal = 194 通过；相关账户服务与集成测试 319 通过（后端子代理，05:2xZ）。账户前端 `npm test` 231 项（含新 9 项）；六个前端工作区 668 项 node 测试、五守卫、图标与 Logo、根 typecheck/build 通过（05:30Z）。初版完整根套件（05:30Z–05:54Z）：**6026 通过、0 失败、2 跳过**。

第一轮对抗性审查（只读子代理）九项，均已修正：(1) 影响按“人”计算——默认创建的账户（个人空间开通 Docs）下收紧与按空间停用的失去访问人数约显示为 0 → 按“人 × 工作空间”对比前后，新增 `memberships_losing_access` / `memberships_gaining_access`，差集绑定摘要，测试逐对对照 `check_access`；(2) Forms 公开与专属填写链接不受 Forms 启用状态控制 → `Filling.resolve` 检查 `check_access_for_workspace`，停用期间读取、草稿、上传、提交均 410 `link_unavailable`；(3) 行为说明缺 Webhook 自动停用、委托在刷新时结束、重新启用需重新授权 → 三个说明码；(4) 平台级变更与按空间同一权限 → D12 两级权限，预览与确认均在严格重认证前检查；(5) 收紧日志与恢复的竞态 → 结果先于服务调用计算，`app_policy_tightened` 改为与 `ai_policy_narrowed` 相同的回执形状并总是重放；(6) 预览占用写锁、列表计数重复 → 延迟读快照、`COUNT(DISTINCT)`；(7) `running_jobs` 含此刻已被拒绝的任务 → 只计“现在允许、变更后拒绝”；(8) 平台停用后“未发布”标记残留，下次发布启动会重新启用 → 平台停用时删除标记；(9) 密钥租户问题提示只提命令行 → 也指向控制台“应用目录”。修正后相关 379 项通过；中间树完整根套件（05:58Z–06:22Z）6044 通过、0 失败、2 跳过（运行期间第二轮修正正在落地，仅作基线）。

第二轮复审（只读子代理，以探针测试复现）三项 + 一项说明，均已修正：(a) 中——恢复备份可重新启用被运维停用的应用：`app_disabled` 重放不删除 `suite_unreleased:` 标记，`bootstrap_suite_app` 下次发布启动即启用 → 重放无条件删除标记，集成测试覆盖“备份时停用 + 标记”与“备份时命令行启用 + 标记”；(b) 中——API 批准每次使用都复核批准者（`Approvals.active` → `require_owner` → `check_access`），收紧后由没有明确授权的拥有者批准的 API 密钥与服务账户同样被拒（`approver_unavailable`），预览少计任务且前端文案称其“不受影响” → `_approvals_stopping` 用同一前后状态计算，新增影响字段 `credentials_stopping` 与说明码 `approved_credentials_stop`，受影响批准绑定摘要（新批准使预览陈旧），前端更正三处“不受影响”文案并显示新计数（三语言）；另排除项目已删除或缺失的任务（运行器本就拒绝）；(c) 低——早于应用登记的备份恢复后收紧与停用都丢失 → 重放先建占位登记行再写限制（失败即关闭；启动登记不改状态、不传策略时不改策略，真实地址由服务该应用的进程下次启动写入）；(d) 失败审计只在应用已登记时记录 `object_id`。修正后定向 181 项（含 test_admin_apps 31 项、Docs 恢复围栏 test_snapshot）+ 相邻 204 项 + 四个探针通过（审批探针补上项目行后通过）；账户前端 232 项、六个前端工作区 669 项 node 测试、五守卫、图标与 Logo、根 typecheck/build 通过（06:23Z）。最终树完整根套件（06:22Z–06:46Z）：**6049 通过、0 失败、2 跳过**。

## 2026-10-13 CR-127 A34 平台只读组织详情（本地）

测试：`services/identity/tests/test_admin_org_detail.py` 11 项 + 七个相关文件 = 187 通过，相邻 113 项通过（后端子代理，06:5xZ）；账户前端 `npm test` 247 项（新 `admin-org-detail.test.mjs` 15 项，三语言、英文渲染无汉字、拒绝码措辞）、根 typecheck/build、check_design/check_component_names/check_brand 通过（前端子代理，07:0xZ）。补上组织权限目录（按 `?lang=`、否则管理员自己的语言）后 66 项通过。

对抗性审查（只读子代理，9 个探针，07:0xZ–07:2xZ）：授权（无 `platform.orgs.view` 的平台角色对存在与不存在的组织同为 403，无存在性探测）、路由顺序、只读快照（`PRAGMA query_only` 下全部路由无写入，嵌套于写事务时加入外层事务）、不泄露名称/清单/地址、租户成员列表行为不变、分组计数与字面搜索均通过。发现并已修正：(1) 中——已删除账户的地址键可被成员搜索匹配（租户原有）→ 只对未删除账户按地址键匹配，新测试先删除账户再搜索旧地址（平台与租户均 0）；(2) 低——`workspaces` 无 `organization_id` 索引 → `ix_ws_org`，测试检查查询计划；(3) 低——过了截止的待批/已批复制仍计为未完成 → 读取时过滤、不写库，测试确认行状态保持供 Docs 清扫；(4) 低——`extracted` 导入行数为 0 → 说明为已规划行数，界面按状态措辞。修正后 org detail 15 项通过；账户前端 247 项、六个前端工作区 684 项 node 测试、五守卫、图标与 Logo、根 typecheck/build、infra 单元测试 12 项通过（07:17Z）。完整根套件（07:17Z–07:44Z）：**6062 通过、2 失败、2 跳过**——两项失败均为 `tests/integration/test_migration_v6.py` 的 v5 升级夹具：它用 `DROP COLUMN workspaces.organization_id` 模拟旧库，被新索引 `ix_ws_org` 阻止。v5 库本无此索引，夹具改为先删索引，并断言升级后索引重建；该文件重跑 2 项通过。生产迁移不删除该列；表重建（`schema_widen`）按存储文本重建索引。

## 2026-10-13 CR-128 充值无法跳转至付款页面（生产只读证据 + 本地修复）

生产只读检查（不调用任何会创建对象的接口、不点击“前往付款”、不打印密钥或账户号）：
- 07:59Z–08:05Z（只读子代理）：镜像 `synwevo:e99212d`；充值订单共 9 笔，收银台地址 0、会话 0、webhook 收件 0、billing_payments 0；5 笔（2026-10-08 13:11Z，旧代码）为 failed/provider_unavailable，4 笔为 creating/provider_unknown（最新 2026-10-13T07:36:13Z）；支付设置版本 1（停用，card）、2（启用，card，仅存在 13 秒，无订单）、3（启用，card + wechat_pay + alipay，全部订单）；`/api/auth/state` 的 challenge 为 null，CSP 不含人机验证域名（排除人机验证阻断）；容器日志无充值相关行（失败原因从未记录）。
- 08:28Z（协调者，只读数据库连接 + 产品就绪检查同样的两次 GET）：`GET /v1/account` 200，响应头 `Stripe-Version: 2026-04-22.dahlia`；香港、个人、默认币种 HKD、charges_enabled；能力 card_payments active，wechat_pay_payments 与 alipay_payments 为空；默认支付方式配置 active，card 可用/开启，wechat_pay 与 alipay 不可用/关闭。密钥类别 sk live（只打印前缀类别）。

本地修复验证（worktree `topup-fix`，基于 8a3762d5）：实现后账户服务 1828、计费集成 272、账户前端 264 通过；新测试在旧代码上失败（Python 33 项、前端 5 个文件）。对抗性审查与修正后：账户服务 1871、计费集成 272、账户前端 273、六个前端工作区 node 测试、五守卫、根 typecheck/build、infra 12 项全部通过，测试期间以网络守卫拦截任何对 Stripe 的请求（无命中）。完整根套件（09:56Z–10:26Z）：**6138 通过、0 失败、2 跳过**。
运营待办（所有者）：控制台“财务与支付 → 支付设置”取消微信支付与支付宝，仅保留银行卡后“检查并发布”；之后在新标签页充值（旧标签页保存的原订单会继续显示核实中，部署本修复后读作已过期）。

