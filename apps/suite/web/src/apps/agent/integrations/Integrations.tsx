/** The MCP integration centre's own routing under /agent/integrations: the list, the add flow,
 *  the client page and a connection's detail. Loaded lazily by the Agent shell. */
import { Link, useLocation, useMatch } from 'react-router-dom';
import { Empty } from '../../../shared/states';
import ClientsPage from './Clients';
import ConnectionDetailPage from './Detail';
import IntegrationsList from './List';
import { CONNECTION_ID } from './model';
import NewConnection from './New';
import { useText } from './shared';
import './integrations.css';

export type IntegrationsRoute = { page: 'list' } | { page: 'new' } | { page: 'clients' } | { page: 'detail'; cid: string } | { page: 'unknown' };
/** Which page a path under /agent/integrations names; an id that is not a connection id is unknown. */
export function routeOf(pathname: string): IntegrationsRoute {
  const rest = pathname.replace(/^\/agent\/integrations\/?/, '').replace(/\/+$/, '');
  if (!rest) return { page: 'list' };
  if (rest === 'new') return { page: 'new' };
  if (rest === 'clients') return { page: 'clients' };
  if (CONNECTION_ID.test(rest)) return { page: 'detail', cid: rest };
  return { page: 'unknown' };
}

export default function Integrations() {
  const { m } = useText();
  const { pathname } = useLocation();
  // Re-mount the detail page per connection so no state leaks from one connection to another.
  const match = useMatch('/agent/integrations/:cid');
  const route = routeOf(pathname);
  switch (route.page) {
    case 'list': return <IntegrationsList />;
    case 'new': return <NewConnection />;
    case 'clients': return <ClientsPage />;
    case 'detail': return <ConnectionDetailPage key={match?.params.cid ?? route.cid} cid={route.cid} />;
    default: return <div className="mi-page col gap16">
      <Empty title={m('notFound')} action={<Link className="btn" to="/agent/integrations">{m('back')}</Link>} />
    </div>;
  }
}
