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
