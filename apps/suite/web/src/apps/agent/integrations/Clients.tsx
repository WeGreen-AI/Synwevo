/** /agent/integrations/clients — what an external MCP client needs to call Synwevo: the real
 *  endpoint, metadata addresses, scopes and protocol versions the server reported, and
 *  configuration examples generated from them. No host is written here. */
import { Link } from 'react-router-dom';
import { Loading } from '../../../shared/states';
import { clientExamples, type InboundInfo } from './model';
import { CopyButton, ErrorNotice, Fact, Facts, useRead, useText } from './shared';

function Example({ title, text, language }: { title: string; text: string; language: string }) {
  return <section className="mi-example" aria-label={title}>
    <div className="mi-example-head"><h3>{title}</h3><CopyButton text={text} /></div>
    <pre className="mi-pre" tabIndex={0} data-language={language}>{text}</pre>
  </section>;
}

export function ClientsView({ info }: { info: InboundInfo }) {
  const { m } = useText();
  const examples = clientExamples(info);
  return <div className="col gap16">
    {!info.enabled && <div className="notice warn col" role="status"><p>{m('inboundClosed')}</p><p>{m('inboundClosedAddresses')}</p></div>}
    <section className="card"><div className="card-b">
      <Facts>
        <Fact label={m('statusLabel')}><span className={`badge ${info.enabled ? 'ok' : 'warn'}`}>{m(info.enabled ? 'inboundOpen' : 'inboundNotOpen')}</span></Fact>
        <Fact label={m('endpoint')}><span className="mi-copy"><code className="mi-break">{info.endpoint}</code><CopyButton text={info.endpoint} /></span></Fact>
        <Fact label={m('transportLabel')}>{info.transport === 'streamable-http' ? m('transportHttp') : info.transport}</Fact>
        <Fact label={m('metadataUrl')}><span className="mi-copy"><code className="mi-break">{info.metadata_url}</code><CopyButton text={info.metadata_url} /></span></Fact>
        <Fact label={m('issuer')}><code className="mi-break">{info.issuer}</code></Fact>
        <Fact label={m('authServer')}><span className="mi-copy"><code className="mi-break">{info.authorization_server_metadata}</code><CopyButton text={info.authorization_server_metadata} /></span></Fact>
        <Fact label={m('scopes')}>{info.scopes.length ? <ul className="mi-list-plain">{info.scopes.map((scope) => <li key={scope}><code>{scope}</code></li>)}</ul> : m('none')}</Fact>
        {!!info.explicit_scopes?.length && <Fact label={m('explicitScopes')}><ul className="mi-list-plain">{info.explicit_scopes.map((scope) => <li key={scope}><code>{scope}</code></li>)}</ul>
          <p className="muted small">{m('explicitScopesNote')}</p></Fact>}
        <Fact label={m('protocols')}>{info.protocol_versions.join(', ')}</Fact>
        <Fact label={m('clientRegistrationLabel')}>{m(info.client_registration === 'cimd_and_preregistered' ? 'registrationCimd' : 'registrationPre')}</Fact>
      </Facts>
      <p className="muted small">{m('pkceNote')}</p>
    </div></section>
    <section className="card"><div className="card-b">
      <h2>{m('examples')}</h2>
      <p className="muted small">{m('examplesNote')}</p>
      <Example title={m('exampleGeneric')} text={examples.generic} language="json" />
      <Example title={m('exampleClaude')} text={examples.claude} language="shell" />
      <Example title={m('examplePython')} text={examples.python} language="python" />
    </div></section>
  </div>;
}

export default function ClientsPage() {
  const { m } = useText();
  const info = useRead<InboundInfo>('agent.mcp.inbound.info');
  return <div className="mi-page col gap16">
    <header className="suite-head"><div>
      <p className="small"><Link to="/agent/integrations">‹ {m('back')}</Link></p>
      <h1>{m('clientsTitle')}</h1>
      <p className="lede">{m('clientsIntro')}</p>
    </div></header>
    {info.loading && !info.data ? <Loading /> : info.error != null ? <ErrorNotice error={info.error} retry={info.reload} />
      : info.data && <ClientsView info={info.data} />}
  </div>;
}
