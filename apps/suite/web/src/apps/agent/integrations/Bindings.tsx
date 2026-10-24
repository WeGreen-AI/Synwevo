/** The "MCP 连接" section of the Agent settings: the connections this person's Agent is set to
 *  use (agent.mcp.binding.list), each with its enable, alias and resources switches. Adding and
 *  granting happen in the integration centre, which the section links to. */
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { Empty, Loading } from '../../../shared/states';
import { slugifyAlias, type Binding, type BindingList, type ConnectionSummary } from './model';
import { ErrorNotice, HealthBadge, OwnerText, useRead, useText, useWrite, WriteFeedback } from './shared';
import './integrations.css';

export function BindingRow({ item, toolCount, onChanged }: { item: ConnectionSummary; toolCount: number; onChanged: (binding: Binding) => void }) {
  const { m } = useText();
  const write = useWrite();
  const binding = item.binding;
  const [alias, setAlias] = useState(binding.alias ?? item.slug);
  const slug = slugifyAlias(alias);
  const ref = { connection_id: item.id };
  function save(input: Record<string, unknown>) {
    write.start<Binding>('agent.mcp.binding.set', { ...ref, ...input }, onChanged);
  }
  function saveAlias(e: FormEvent) {
    e.preventDefault();
    if (write.locked || !slug || slug === binding.alias) return;
    save({ alias: slug });
  }
  return <article className="suite-row wrap">
    <div className="col grow gap4">
      <div className="row wrap"><Link className="title" to={`/agent/integrations/${item.id}`}>{item.name}</Link>
        <HealthBadge health={item.health} />
        {item.status === 'disabled' && <span className="badge warn">{m('statusDisabled')}</span>}
        {binding.enabled ? <span className="badge info">{m('bound')}</span> : <span className="badge">{m('boundDisabled')}</span>}</div>
      <div className="sub"><OwnerText summary={item} /> · {item.host ?? item.url} · {m('effectiveTools', { n: toolCount })}</div>
      <div className="row wrap">
        <label className="check"><input type="checkbox" checked={!!binding.enabled} disabled={write.locked} onChange={(e) => save({ enabled: e.target.checked })} /><span>{m('enabled')}</span></label>
        <label className="check"><input type="checkbox" checked={!!binding.resources_enabled} disabled={write.locked} onChange={(e) => save({ resources_enabled: e.target.checked })} /><span>{m('resourcesEnabled')}</span></label>
      </div>
      <form className="row wrap" onSubmit={saveAlias}>
        <label className="field grow"><span>{m('alias')}</span>
          <input className="input" value={alias} maxLength={24} autoComplete="off" spellCheck={false} disabled={write.locked} onChange={(e) => { setAlias(e.target.value); write.dismiss(); }} /></label>
        <button type="submit" className="btn sm" disabled={write.locked || !slug || slug === binding.alias}>{m('save')}</button>
      </form>
      {!slug && <p className="notice err" role="alert">{m('aliasInvalid')}</p>}
      <WriteFeedback write={write} />
    </div>
  </article>;
}

export default function McpBindings() {
  const { m } = useText();
  const resource = useRead<BindingList>('agent.mcp.binding.list');
  const items = resource.data?.items;
  const tools = resource.data?.tools ?? [];
  return <section className="card" aria-labelledby="ag-mcp-title">
    <div className="card-h"><h2 id="ag-mcp-title">{m('bindingsTitle')}</h2><span className="spacer" />
      <Link className="btn ghost sm" to="/agent/integrations">{m('openCentre')}</Link></div>
    <div className="card-b col gap16">
      <p className="muted">{m('bindingsIntro')}</p>
      {resource.loading && !items ? <Loading /> : resource.error != null ? <ErrorNotice error={resource.error} retry={resource.reload} />
        : items && (items.length === 0 ? <Empty title={m('bindingsEmpty')} action={<Link className="btn" to="/agent/integrations">{m('openCentre')}</Link>} />
          : <div className="suite-list">{items.map((item) => <BindingRow key={item.id} item={item}
            toolCount={tools.filter((tool) => tool.alias === item.binding.alias).length}
            onChanged={(binding) => resource.setData((old) => old && ({ ...old, items: old.items.map((c) => c.id === item.id ? { ...c, binding } : c) }))} />)}</div>)}
    </div>
  </section>;
}
