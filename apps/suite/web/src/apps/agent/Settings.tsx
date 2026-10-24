import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@synwevo/auth-client";
import { useDialog, useI18n } from "@synwevo/ui";
import { newKey, op, read, type RunStatus } from "../../shared/ops";
import { Empty, Failure, Loading, RunBadge } from "../../shared/states";
import PayerBudget from "./PayerBudget";
import ProviderConsent from "./ProviderConsent";
import McpBindings from "./integrations/Bindings";
import "./settings/messages";
import "./messages";

type Effort = "low" | "medium" | "high" | "xhigh" | "max";
type Processing = "none" | "platform" | "any";
type Outbound = "none" | "audience" | "organization" | "any";
type ModelProvider = "anthropic" | "deepseek";
const providerNames: Record<ModelProvider, string> = { anthropic: "Anthropic", deepseek: "DeepSeek" };
interface AgentSettings {
  effort: Effort;
  prefer_personal_key: boolean;
  memory_enabled: boolean;
  personal_key: { set: boolean };
  personal_key_supported: boolean;
  billing: { required: boolean };
  model_configured: boolean;
  platform_configured: boolean;
  active: { credential: "platform" | "personal"; model: string; provider: ModelProvider } | null;
  efforts: Effort[];
}
interface Memory { id: string; body: string }
interface TransferPolicy {
  outbound: Outbound; explicit: boolean; revision: number;
  in_organization: boolean; organization_outbound: "organization" | "any";
}
interface WorkspacePolicy {
  workspace_id: string; name: string; kind: string; role: string;
  processing: Processing; explicit: boolean; can_change: boolean;
  providers: ModelProvider[]; providers_explicit: boolean;
  transfer: TransferPolicy;
}

const effortLabels: Record<Effort, string> = {
  low: "较低", medium: "中等", high: "较高", xhigh: "很高", max: "最高",
};
const policyLabels: Record<Processing, string> = {
  none: "不允许模型处理", platform: "仅平台凭据", any: "平台或个人凭据",
};
const policyImpact: Record<Processing, string> = {
  none: "此空间的业务内容不得发送给任何模型提供方；已获授权的应用操作仍可执行。",
  platform: "仅使用平台凭据，将获授权的业务内容发送给此空间允许的提供方。个人密钥不能处理此空间内容。",
  any: "可使用平台或个人凭据，将获授权的业务内容发送给此空间允许的提供方；具体凭据仍须受当前模型服务支持。",
};

const outboundLabels: Record<Outbound, string> = {
  none: "不外发", audience: "仅限已有阅读者", organization: "同一组织内", any: "转移人可写的任何空间",
};
const outboundImpact: Record<Outbound, string> = {
  none: "此空间的内容不能被 Agent、流程或表单复制带入其他工作空间。",
  audience: "只能带入其全部成员都已是本空间成员的工作空间：没有人因此看到原本看不到的内容。",
  organization: "只能带入同一组织的工作空间。",
  any: "可以带入转移人有写入权限的任何工作空间。",
};

/** Do not render server prose or validation details: they may include submitted secrets. */
function ErrorNotice({ error, retry }: { error: unknown; retry?: () => void }) {
  const { t } = useI18n();
  const code = error instanceof ApiError
    ? error.code === "idempotent_failure" ? error.detail?.code : error.code
    : null;
  const messages: Record<string, string> = {
    bad_key: "密钥格式不正确，请输入 20–300 个不含空白的字符。",
    model_personal_key_unsupported: "当前付费模型服务不支持个人密钥，可清除已保存的旧密钥。",
    invalid_input: "输入不符合要求，请检查后再提交。",
    too_many_memories: "最多保存 50 条个人记忆，请先删除一些。",
    memory_not_found: "这条记忆已不存在，请重新加载记忆列表。",
    content_blocked_memory: "这条记忆触发了内容安全规则，没有保存。请修改后再保存。",
    idempotency_conflict: "这次请求与先前的操作不一致，请重新加载并核对结果。",
    transfer_policy_changed: "这个工作空间的数据流政策刚被其他人修改，本次更改没有保存。请重新加载后核对当前政策再决定。",
    account_unavailable: "登录已失效，请重新登录。",
    app_unavailable: "Agent 目前不可用，请联系平台管理员。",
    unknown_operation: "服务器暂不支持此操作，请联系平台管理员。",
    app_access_denied: "你目前无权执行此操作，请重新加载以核对权限。",
    forbidden: "你目前无权执行此操作，请重新加载以核对权限。",
    network: "连接中断，尚未收到服务器结果。",
    upstream_unavailable: "暂时无法获得服务器响应，请稍后重试。",
    gateway_blocked: "请求被前置网关拦截，请联系平台管理员。",
  };
  const text = code && messages[code] ? t(messages[code])
    : error instanceof ApiError && error.status === 401 ? t("登录已失效，请重新登录。")
    : error instanceof ApiError && error.status === 403 ? t("你目前无权执行此操作，请重新加载以核对权限。")
    : error instanceof ApiError && error.status === 429 ? t("请求过于频繁，请稍后重试。")
    : t("未能完成请求，请重试或联系平台管理员。");
  // Preserve only safe diagnostics for the shared Failure presentation.
  const safe = new ApiError(error instanceof ApiError ? error.status : 0, "agent_settings_error", "");
  Object.defineProperty(safe, "message", { get: () => text });
  if (error instanceof ApiError) safe.requestId = error.requestId;
  return <>
    <Failure error={safe} onRetry={retry} />
    {error instanceof ApiError && error.retryAfter != null && <p className="muted small">
      {t("请在 {seconds} 秒后重试。", { seconds: error.retryAfter })}</p>}
  </>;
}

function useRead<T>(operation: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((n) => n + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void read<T>(operation, {}, null, controller.signal).then((value) => {
      if (!controller.signal.aborted) setData(value);
    }).catch((e: unknown) => {
      if (!controller.signal.aborted) setError(e);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [operation, revision]);
  return { data, setData, error, loading, reload };
}

interface Intent {
  operation: string; input: Record<string, unknown>; workspace: string | null; key: string;
  saved: (output: unknown) => void;
}
interface WriteState {
  busy: boolean; pending: boolean; error: unknown; status: RunStatus | null; saved: boolean;
}

/** One intent at a time. A lost response retains both the exact payload and its key in memory.
 *  Never persist this ref: a pending personal-key request contains a secret. */
function useWrite() {
  const intent = useRef<Intent | null>(null);
  const flight = useRef(false);
  const live = useRef(true);
  const [state, setState] = useState<WriteState>({ busy: false, pending: false, error: null, status: null, saved: false });
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; intent.current = null; };
  }, []);

  async function execute() {
    const request = intent.current;
    if (!request || flight.current) return;
    flight.current = true;
    setState({ busy: true, pending: true, error: null, status: null, saved: false });
    try {
      const result = await op<unknown>(request.operation, request.input, { workspace: request.workspace, key: request.key });
      if (!live.current) return;
      if (result.status === "succeeded") {
        intent.current = null;
        setState({ busy: false, pending: false, error: null, status: null, saved: true });
        request.saved(result.output);
      } else {
        const pending = !["failed", "cancelled", "partial"].includes(result.status);
        if (!pending) intent.current = null;
        setState({ busy: false, pending, error: null, status: result.status, saved: false });
      }
    } catch (error) {
      if (!live.current) return;
      // A received application refusal is definitive. Network, gateway, parsing and 5xx errors
      // can happen after a successful commit, so they must retain the original request.
      const pending = !(error instanceof ApiError && !error.upstream && error.status >= 400 && error.status < 500);
      if (!pending) intent.current = null;
      setState({ busy: false, pending, error, status: null, saved: false });
    } finally {
      flight.current = false;
    }
  }

  function start<T>(operation: string, input: Record<string, unknown>, saved: (output: T) => void, workspace: string | null = null) {
    if (intent.current || flight.current) return;
    intent.current = { operation, input: { ...input }, workspace, key: newKey(), saved: (output) => saved(output as T) };
    void execute();
  }
  return { ...state, locked: state.busy || state.pending, start, retry: () => void execute(),
    dismiss: () => setState((s) => s.pending ? s : { ...s, saved: false, error: null, status: null }) };
}

function WriteFeedback({ write }: { write: ReturnType<typeof useWrite> }) {
  const { t } = useI18n();
  return <div aria-live="polite">
    {write.busy && <p role="status">{t("正在保存更改…")}</p>}
    {write.error != null && <ErrorNotice error={write.error} />}
    {write.status && <p><RunBadge status={write.status} /></p>}
    {write.pending && !write.busy && <div className="notice warn col">
      <p>{t("尚未确认这次更改的最终结果。重试会核对同一次操作，不会重复创建记录。核对前请保持此页面打开。")}</p>
      <div><button type="button" className="btn" onClick={write.retry}>{t("重试同一次操作")}</button></div>
    </div>}
    {write.saved && <p className="notice ok" role="status">{t("更改已保存。")}</p>}
  </div>;
}

function AccessSettings() {
  const { t } = useI18n();
  const resource = useRead<AgentSettings>("agent.settings.get");
  return <><section className="card" aria-labelledby="ag-access-title">
    <div className="card-h"><h2 id="ag-access-title">{t("模型服务与个人偏好")}</h2></div>
    <div className="card-b col gap16">
      {resource.loading ? <Loading /> : resource.error != null ? <ErrorNotice error={resource.error} retry={resource.reload} />
        : resource.data && <AccessForm settings={resource.data} onChanged={resource.setData} />}
    </div>
  </section>
    {resource.data?.billing.required && <PayerBudget />}
  </>;
}

function AccessForm({ settings, onChanged }: { settings: AgentSettings; onChanged: (value: AgentSettings) => void }) {
  const { t } = useI18n();
  const dialog = useDialog();
  const write = useWrite();
  const [key, setKey] = useState("");
  const [effort, setEffort] = useState(settings.effort);
  const [prefer, setPrefer] = useState(settings.prefer_personal_key);
  const [memory, setMemory] = useState(settings.memory_enabled);
  const [confirming, setConfirming] = useState(false);
  const locked = write.locked || confirming;
  const changed = effort !== settings.effort || prefer !== settings.prefer_personal_key || memory !== settings.memory_enabled;
  const cleanKey = key.trim();
  const validKey = cleanKey.length >= 20 && cleanKey.length <= 300 && !/\s/.test(cleanKey);

  function savePreferences() {
    if (locked || !changed) return;
    write.start<AgentSettings>("agent.settings.update", { effort, memory_enabled: memory,
      ...(settings.personal_key_supported ? { prefer_personal_key: prefer } : {}) }, (value) => {
      onChanged(value);
      setEffort(value.effort); setPrefer(value.prefer_personal_key); setMemory(value.memory_enabled);
    });
  }
  async function clearKey() {
    if (locked) return;
    setConfirming(true);
    try {
      if (!await dialog.confirm(t("清除个人密钥"), t("从你的 Agent 设置中移除已保存的密钥。之后将尝试使用平台凭据；若模型或平台凭据未配置，Agent 将无法回答。此操作不会在模型服务商处撤销密钥。"), t("清除个人密钥"), true)) return;
      setKey("");
      write.start<AgentSettings>("agent.settings.update", { clear_personal_key: true }, onChanged);
    } finally { setConfirming(false); }
  }

  return <>
    <div className="col">
      <div className="row wrap"><strong>{t("模型服务商")}</strong><span>{settings.active ? providerNames[settings.active.provider] : t("未配置")}</span></div>
      <div className="row wrap"><span>{t("模型配置")}</span>
        <span className={`badge ${settings.model_configured ? "info" : "warn"}`}>
          {settings.model_configured ? t("已配置") : t("未配置")}</span></div>
      <div className="row wrap"><span>{t("平台模型服务")}</span>
        <span className={`badge ${settings.platform_configured ? "info" : "warn"}`}>
          {settings.platform_configured ? t("已配置") : t("未配置")}</span></div>
      <div className="row wrap"><span>{t("个人密钥")}</span>
        <span className="badge">{settings.personal_key.set ? t("已保存") : t("未设置")}</span></div>
      {settings.active ? <>
        <div className="row wrap"><span>{t("当前模型")}</span><code>{settings.active.model}</code></div>
        <div className="row wrap"><span>{t("当前使用的凭据")}</span><strong>
          {settings.active.credential === "personal" ? t("个人密钥") : t("平台凭据")}</strong></div>
      </> : <p className="notice warn">{!settings.model_configured
        ? t("平台尚未指定模型，Agent 无法回答。请联系平台管理员配置模型；单独保存个人密钥不能启用模型服务。")
        : settings.personal_key_supported ? t("Agent 目前无法使用模型。请检查个人密钥，或请平台管理员核对模型与平台凭据配置。")
        : t("Agent 目前无法使用模型，请平台管理员核对模型与平台凭据配置。")}</p>}
      {settings.personal_key_supported && <p className="muted small">{t("模型由平台管理员指定，个人密钥使用同一模型。")}</p>}
      <p className="muted small">{t("配置状态不代表密钥已通过模型服务商验证；实际可用性以请求结果为准。")}</p>
    </div>

    <form className="col" onSubmit={(e) => {
      e.preventDefault();
      if (locked || !settings.personal_key_supported || !validKey) return;
      // Empty the visible field immediately; only the pending request retains the secret for retry.
      setKey("");
      write.start<AgentSettings>("agent.settings.update", { personal_key: cleanKey }, onChanged);
    }}>
      <label className="field"><span>{settings.personal_key.set ? t("替换个人密钥") : t("设置个人密钥")}</span>
        <input className="input" type="password" autoComplete="new-password" spellCheck={false} autoCapitalize="none"
          value={key} maxLength={300} disabled={locked || !settings.personal_key_supported} aria-describedby="ag-key-help"
          onChange={(e) => { setKey(e.target.value); write.dismiss(); }} /></label>
      <p id="ag-key-help" className="muted small">{settings.personal_key_supported
        ? t("输入 Anthropic API 密钥。密钥在服务端加密保存，保存后不会在此页面回显。密钥长度为 20–300 个字符，不含空白。")
        : t("当前付费模型服务不支持个人密钥，可清除已保存的旧密钥。")}</p>
      <div className="row wrap">
        <button type="submit" className="btn" disabled={locked || !settings.personal_key_supported || !validKey}>{t("保存个人密钥")}</button>
        {settings.personal_key.set && <button type="button" className="btn danger" disabled={locked} onClick={() => void clearKey()}>{t("清除个人密钥")}</button>}
      </div>
    </form>

    <form className="col gap12" onSubmit={(e) => { e.preventDefault(); savePreferences(); }}>
      <label className="check"><input type="checkbox" checked={prefer} disabled={locked || !settings.personal_key_supported}
        onChange={(e) => { setPrefer(e.target.checked); write.dismiss(); }} /><span>{t("优先使用个人密钥")}</span></label>
      <p className="muted small">{settings.personal_key_supported
        ? t("启用后优先使用已保存且可解密的个人密钥，否则使用平台凭据。未启用时优先使用平台凭据；平台未配置时仍会尝试个人密钥。平台专用的工作空间不会自动切换凭据。")
        : t("已保存的个人密钥偏好保留，但在当前付费模型服务中不可用。请求使用平台凭据并按已确认的报价收费。")}</p>
      <label className="field"><span>{t("推理投入")}</span>
        <select className="input" value={effort} disabled={locked} onChange={(e) => { setEffort(e.target.value as Effort); write.dismiss(); }}>
          {settings.efforts.map((value) => <option key={value} value={value}>{t(effortLabels[value] ?? value)}</option>)}
        </select></label>
      <p className="muted small">{t("更高的推理投入可能增加响应时间和模型费用。")}</p>
      <label className="check"><input type="checkbox" checked={memory} disabled={locked}
        onChange={(e) => { setMemory(e.target.checked); write.dismiss(); }} /><span>{t("在回答中使用个人记忆")}</span></label>
      <p className="muted small">{t("停用后，后续回答不会额外加载已保存的个人记忆。你仍可在下方查看、编辑或删除记忆。正在生成的回答、已有对话和已发送给模型的内容不受影响。")}</p>
      <div className="row wrap"><button type="submit" className="btn primary" disabled={locked || !changed}>{t("保存个人偏好")}</button>
        {changed && <span className="muted small">{t("个人偏好尚未保存")}</span>}</div>
    </form>
    <WriteFeedback write={write} />
  </>;
}

function Memories() {
  const { t } = useI18n();
  const dialog = useDialog();
  const resource = useRead<{ memories: Memory[] }>("agent.memory.list");
  const write = useWrite();
  const [body, setBody] = useState("");
  const [edit, setEdit] = useState<Memory | null>(null);
  const [confirming, setConfirming] = useState(false);
  const locked = write.locked || confirming || resource.loading;
  const memories = resource.data?.memories;

  async function remove(memory: Memory) {
    if (locked) return;
    setConfirming(true);
    try {
      if (!await dialog.confirm(t("删除个人记忆"), <div className="col">
        <p>{t("从你的个人记忆中永久删除以下内容？这不会删除已有对话或模型服务商已收到的内容。")}</p>
        <blockquote>{memory.body}</blockquote>
      </div>, t("删除"), true)) return;
      write.start<{ deleted: string }>("agent.memory.delete", { memory_id: memory.id }, (out) => {
        resource.setData((old) => old && ({ memories: old.memories.filter((m) => m.id !== out.deleted) }));
        setEdit((old) => old?.id === out.deleted ? null : old);
      });
    } finally { setConfirming(false); }
  }

  return <section className="card" aria-labelledby="ag-memory-title">
    <div className="card-h"><h2 id="ag-memory-title">{t("个人记忆")}</h2><span className="spacer" />
      <button type="button" className="btn ghost sm" disabled={locked || edit !== null}
        onClick={() => { write.dismiss(); resource.reload(); }}>{t("重新加载记忆")}</button></div>
    <div className="card-b col gap16">
      <p className="muted">{t("只保存你希望 Agent 记住的个人偏好，不要在此保存工作空间业务资料或密钥。启用个人记忆时，这些内容会随请求发送给当前模型提供方。")}</p>
      {resource.loading ? <Loading /> : resource.error != null ? <ErrorNotice error={resource.error} retry={resource.reload} /> : memories && <>
        <p className="muted small">{t("已保存 {n} / 50 条记忆，每条最多 500 个字符。", { n: memories.length })}</p>
        {memories.length === 0 ? <Empty title={t("还没有个人记忆")} /> : <div className="suite-list">
          {memories.map((memory) => <div className="suite-row wrap" key={memory.id}>
            {edit?.id === memory.id ? <form className="grow col" onSubmit={(e) => {
              e.preventDefault();
              const value = edit.body.trim();
              if (locked || !value || value.length > 500 || value === memory.body) return;
              write.start<Memory>("agent.memory.update", { memory_id: memory.id, body: value }, (out) => {
                resource.setData((old) => old && ({ memories: old.memories.map((m) => m.id === out.id ? { ...m, body: out.body } : m) }));
                setEdit(null);
              });
            }}>
              <label className="field"><span>{t("编辑个人记忆")}</span><textarea className="input" rows={3} maxLength={500}
                value={edit.body} disabled={locked} onChange={(e) => { setEdit({ id: memory.id, body: e.target.value }); write.dismiss(); }} /></label>
              <div className="row wrap"><button type="submit" className="btn primary sm" disabled={locked || !edit.body.trim() || edit.body.trim() === memory.body}>{t("保存")}</button>
                <button type="button" className="btn sm" disabled={locked} onClick={() => setEdit(null)}>{t("取消")}</button></div>
            </form> : <>
              <p className="grow">{memory.body}</p>
              <div className="row wrap"><button type="button" className="btn sm" disabled={locked || edit !== null}
                onClick={() => { setEdit(memory); write.dismiss(); }}>{t("编辑个人记忆")}</button>
                <button type="button" className="btn ghost sm" disabled={locked || edit !== null} onClick={() => void remove(memory)}>{t("删除个人记忆")}</button></div>
            </>}
          </div>)}
        </div>}
        <form className="col" onSubmit={(e) => {
          e.preventDefault();
          const value = body.trim();
          if (locked || edit || !value || value.length > 500 || memories.length >= 50) return;
          write.start<Memory>("agent.memory.add", { body: value }, (out) => {
            resource.setData((old) => old && ({ memories: [...old.memories.filter((m) => m.id !== out.id), out] }));
            setBody("");
          });
        }}>
          <label className="field"><span>{t("添加个人记忆")}</span><textarea className="input" rows={3} maxLength={500}
            disabled={locked || edit !== null || memories.length >= 50} value={body}
            onChange={(e) => { setBody(e.target.value); write.dismiss(); }} /></label>
          <div><button type="submit" className="btn" disabled={locked || edit !== null || !body.trim() || memories.length >= 50}>{t("添加个人记忆")}</button></div>
        </form>
      </>}
      <WriteFeedback write={write} />
    </div>
  </section>;
}

type PolicySaved = Pick<WorkspacePolicy, "processing" | "providers" | "providers_explicit">;
function PolicyRow({ workspace, onChanged }: { workspace: WorkspacePolicy; onChanged: (value: PolicySaved) => void }) {
  const { t } = useI18n();
  const dialog = useDialog();
  const write = useWrite();
  const [processing, setProcessing] = useState(workspace.processing);
  const [providers, setProviders] = useState(workspace.providers);
  const [confirming, setConfirming] = useState(false);
  const locked = write.locked || confirming;
  const changed = processing !== workspace.processing || providers.length !== workspace.providers.length
    || providers.some((p) => !workspace.providers.includes(p));
  const providerLabel = (values: ModelProvider[]) => values.length ? values.map((p) => providerNames[p]).join(", ") : t("未允许任何提供方");

  async function save() {
    if (locked || !workspace.can_change || !changed) return;
    const proposed = processing;
    const proposedProviders = [...providers];
    setConfirming(true);
    try {
      if (!await dialog.confirm(t("确认更改工作空间处理政策"), <div className="col">
        <p>{t("目标工作空间：{name}", { name: workspace.name })}</p><code>{workspace.workspace_id}</code>
        <p>{t("从「{before}」更改为「{after}」。", { before: t(policyLabels[workspace.processing]), after: t(policyLabels[proposed]) })}</p>
        <p>{t("提供方范围从「{before}」更改为「{after}」。", { before: providerLabel(workspace.providers), after: providerLabel(proposedProviders) })}</p>
        <p>{t(policyImpact[proposed])}</p>
        <p>{t("此政策影响该空间的所有成员，不会扩大任何人的业务访问权限，也无法收回模型服务商已经收到的内容。")}</p>
      </div>, t("确认更改政策"), true)) return;
      write.start<PolicySaved>("agent.workspace_policy.set", { processing: proposed, providers: proposedProviders }, (out) => {
        onChanged(out); setProcessing(out.processing); setProviders(out.providers);
      }, workspace.workspace_id);
    } finally { setConfirming(false); }
  }

  return <article className="suite-row">
    <div className="col grow">
      <h3>{workspace.name}</h3><code>{workspace.workspace_id}</code>
      <div className="row wrap"><strong>{t(policyLabels[workspace.processing])}</strong>
        <span className="badge">{workspace.explicit ? t("已明确设置") : t("默认政策")}</span></div>
      <p className="muted small">{t(policyImpact[workspace.processing])}</p>
      <p>{t("提供方范围：{providers}", { providers: providerLabel(workspace.providers) })}</p>
      {!workspace.providers_explicit && <p className="muted small">{workspace.kind === "personal" && !workspace.explicit
        ? t("个人空间尚未设置政策，默认跟随个人的提供方选择。保存后仅允许勾选的提供方。")
        : t("旧政策仅允许 Anthropic，启用 DeepSeek 需要所有者或管理员明确选择。")}</p>}
      {workspace.can_change ? <form className="col" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <label className="field"><span>{t("{name} 的模型处理政策", { name: workspace.name })}</span>
          <select className="input" value={processing} disabled={locked}
            onChange={(e) => { setProcessing(e.target.value as Processing); write.dismiss(); }}>
            {(["none", "platform", "any"] as const).map((value) => <option key={value} value={value}>{t(policyLabels[value])}</option>)}
          </select></label>
        <fieldset className="col" disabled={locked}>
          <legend>{t("允许处理此工作空间内容的提供方")}</legend>
          {(Object.keys(providerNames) as ModelProvider[]).map((provider) => <label className="check" key={provider}>
            <input type="checkbox" name="provider" value={provider} checked={providers.includes(provider)}
              onChange={(e) => { setProviders((old) => e.target.checked ? [...old, provider].sort() : old.filter((p) => p !== provider)); write.dismiss(); }} />
            <span>{providerNames[provider]}</span>
          </label>)}
        </fieldset>
        <p className="muted small">{t("提供方范围与凭据来源同时生效。新增提供方不会自动获得许可；未选择任何提供方时全部拒绝。")}</p>
        {processing !== workspace.processing && <p className="muted small">{t(policyImpact[processing])}</p>}
        <div><button type="submit" className="btn" disabled={locked || !changed}>{t("更改处理政策")}</button></div>
      </form> : <p className="muted small">{t("仅此工作空间的所有者或管理员可以更改政策。")}</p>}
      <WriteFeedback write={write} />
    </div>
  </article>;
}

function TransferRow({ workspace, onChanged }: { workspace: WorkspacePolicy; onChanged: (value: TransferPolicy) => void }) {
  const { t } = useI18n();
  const dialog = useDialog();
  const write = useWrite();
  const current = workspace.transfer;
  const [outbound, setOutbound] = useState(current.outbound);
  const [confirming, setConfirming] = useState(false);
  const locked = write.locked || confirming;
  // A default follows the workspace (a personal workspace becomes "audience" once shared, and
  // returns to "any" when it is not): choosing the same value pins it as an explicit decision.
  const pin = outbound === current.outbound && !current.explicit;
  const unchanged = outbound === current.outbound && current.explicit;
  // "Same organization" can never be met by a workspace outside one; it stays visible only if already chosen.
  const choices = (["none", "audience", "organization", "any"] as const)
    .filter((value) => value !== "organization" || current.in_organization || current.outbound === "organization");

  async function save() {
    if (locked || !workspace.can_change || unchanged) return;
    const proposed = outbound;
    setConfirming(true);
    try {
      if (!await dialog.confirm(t("确认更改数据流政策"), <div className="col">
        <p>{t("目标工作空间：{name}", { name: workspace.name })}</p><code>{workspace.workspace_id}</code>
        <p>{pin ? t("把默认政策「{policy}」明确设为本工作空间的政策：之后成员变化不会再改变它。", { policy: t(outboundLabels[proposed]) })
          : t("从「{before}」更改为「{after}」。", { before: t(outboundLabels[current.outbound]), after: t(outboundLabels[proposed]) })}</p>
        <p>{t(outboundImpact[proposed])}</p>
        <p>{t("新政策只约束之后的转移；已转移到其他空间的独立记录按目标空间的权限管理。")}</p>
      </div>, t("确认更改政策"), true)) return;
      write.start<TransferPolicy>("agent.transfer_policy.set", { outbound: proposed, revision: current.revision }, (out) => {
        onChanged({ ...current, ...out }); setOutbound(out.outbound);
      }, workspace.workspace_id);
    } finally { setConfirming(false); }
  }

  return <article className="suite-row">
    <div className="col grow">
      <h3>{workspace.name}</h3><code>{workspace.workspace_id}</code>
      <div className="row wrap"><strong>{t(outboundLabels[current.outbound])}</strong>
        <span className="badge">{current.explicit ? t("已明确设置") : t("默认政策")}</span></div>
      <p className="muted small">{t(outboundImpact[current.outbound])}</p>
      {current.in_organization && current.organization_outbound === "organization" &&
        <p className="muted small">{t("所属组织只允许内容留在组织内：组织以外的目标仍会被拒绝。")}</p>}
      {workspace.can_change ? <form className="col" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <label className="field"><span>{t("{name} 的数据流政策", { name: workspace.name })}</span>
          <select className="input" value={outbound} disabled={locked}
            onChange={(e) => { setOutbound(e.target.value as Outbound); write.dismiss(); }}>
            {choices.map((value) => <option key={value} value={value}>{t(outboundLabels[value])}</option>)}
          </select></label>
        {outbound !== current.outbound && <p className="muted small">{t(outboundImpact[outbound])}</p>}
        <div><button type="submit" className="btn" disabled={locked || unchanged}>{pin ? t("明确设置为此政策") : t("更改数据流政策")}</button></div>
      </form> : <p className="muted small">{t("仅此工作空间的所有者或管理员可以更改政策。")}</p>}
      <WriteFeedback write={write} />
    </div>
  </article>;
}

function TransferPolicies() {
  const { t } = useI18n();
  const resource = useRead<{ workspaces: WorkspacePolicy[] }>("agent.workspace_policy.list");
  return <section className="card" aria-labelledby="ag-transfer-title">
    <div className="card-h"><h2 id="ag-transfer-title">{t("工作空间的数据流政策")}</h2></div>
    <div className="card-b col gap16">
      <p className="muted">{t("决定各工作空间的内容能否被 Agent、流程或表单复制带入其他工作空间。能同时阅读两个空间不等于可以转移：转移人还需要来源空间的导出权限（所有者、管理员、编辑者，或被授予导出权限的成员）。")}</p>
      {resource.loading ? <Loading /> : resource.error != null ? <ErrorNotice error={resource.error} retry={resource.reload} />
        : resource.data?.workspaces.length === 0 ? <Empty title={t("没有可显示的工作空间政策")} />
        : <div className="suite-list">{resource.data?.workspaces.map((workspace) => <TransferRow key={workspace.workspace_id} workspace={workspace}
          onChanged={(transfer) => resource.setData((old) => old && ({ workspaces: old.workspaces.map((w) =>
            w.workspace_id === workspace.workspace_id ? { ...w, transfer } : w) }))} />)}</div>}
    </div>
  </section>;
}

function WorkspacePolicies() {
  const { t } = useI18n();
  const resource = useRead<{ workspaces: WorkspacePolicy[] }>("agent.workspace_policy.list");
  return <section className="card" aria-labelledby="ag-policy-title">
    <div className="card-h"><h2 id="ag-policy-title">{t("工作空间的模型处理政策")}</h2></div>
    <div className="card-b col gap16">
      <p className="muted">{t("决定 Agent 是否可以通过应用操作将各工作空间的业务内容交给模型服务商处理。政策按空间生效，与个人模型偏好分开保存。")}</p>
      {resource.loading ? <Loading /> : resource.error != null ? <ErrorNotice error={resource.error} retry={resource.reload} />
        : resource.data?.workspaces.length === 0 ? <Empty title={t("没有可显示的工作空间政策")} />
        : <div className="suite-list">{resource.data?.workspaces.map((workspace) => <PolicyRow key={workspace.workspace_id} workspace={workspace}
          onChanged={(policy) => resource.setData((old) => old && ({ workspaces: old.workspaces.map((w) =>
            w.workspace_id === workspace.workspace_id ? { ...w, ...policy, explicit: true } : w) }))} />)}</div>}
    </div>
  </section>;
}

export default function Settings() {
  const { t } = useI18n();
  return <div className="ag-settings col gap16">
    <header className="suite-head"><div><h1>{t("Agent 设置")}</h1>
      <p className="lede">{t("管理你的模型访问、个人记忆，以及获授权管理的工作空间处理政策。")}</p></div></header>
    <ProviderConsent />
    <AccessSettings />
    <Memories />
    <McpBindings />
    <WorkspacePolicies />
    <TransferPolicies />
  </div>;
}
