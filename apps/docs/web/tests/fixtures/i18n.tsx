/** Isolated browser fixture. Real components, no accounts, no API and no business data. */
import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom';
import { DialogProvider, I18nProvider, LanguageMenu, registerMessages, ToastProvider, useI18n } from '@synwevo/ui';
import { messages } from '../../src/messages';
import '../../src/lib/presentation';
import DocEditor from '../../src/pages/DocEditor';
import SheetEditor from '../../src/pages/SheetEditor';
import Transforms from '../../src/components/Transforms';
import { ApiImage, DownloadLink } from '../../src/components/ApiResource';
import { setWorkspaceContext } from '@synwevo/auth-client';
import '@synwevo/ui/styles.css';
import '../../src/styles.css';
registerMessages(messages);
let mounts = 0;
function Probe() {
  const { t } = useI18n();
  const [value, setValue] = useState('夹具原文：客户名称 <b>{count}</b>');
  const id = useRef(++mounts);
  useEffect(() => { document.body.dataset.probeMount = String(id.current); }, []);
  const [rules, setRules] = useState<any[]>([{ op: 'map', table: { TRUE: '是', FALSE: '否' } }]);
  return <section><label>{t('名称')}<input data-testid="state-input" value={value} onChange={e => setValue(e.target.value)} /></label><output>{t('{count} 个项目', { count: 1 })}</output><Transforms value={rules} onChange={setRules} /><pre data-testid="rules">{JSON.stringify(rules)}</pre></section>;
}
function Fixture() {
  return <I18nProvider title="Synwevo Docs · 文档工作台"><BrowserRouter><ToastProvider><DialogProvider>
    <aside data-testid="fixture-controls"><strong>Isolated test fixture — no business API</strong><LanguageMenu /><Link to="/tests/fixtures/i18n.html">Probe</Link> · <Link to="/tests/fixtures/i18n.html/doc">Document</Link> · <Link to="/tests/fixtures/i18n.html/sheet">Spreadsheet</Link> · <Link to="/tests/fixtures/i18n.html/resources">Resources</Link></aside>
    <Routes><Route path="/tests/fixtures/i18n.html/doc" element={<DocEditor />} /><Route path="/tests/fixtures/i18n.html/sheet" element={<SheetEditor />} /><Route path="/tests/fixtures/i18n.html/resources" element={<ResourceProbe />} /><Route path="*" element={<Probe />} /></Routes>
  </DialogProvider></ToastProvider></BrowserRouter></I18nProvider>;
}
function ResourceProbe() {
  const [ready, setReady] = useState(false);
  useEffect(() => { setWorkspaceContext('fixture-tab-A'); setReady(true); return () => setWorkspaceContext(null); }, []);
  return ready ? <section><DownloadLink href="/api/i18n-fixture/download">Download fixture</DownloadLink>
    <DownloadLink href="/api/i18n-fixture/denied">Denied fixture</DownloadLink>
    <ApiImage src="/api/i18n-fixture/image" alt="Fixture image" /></section> : null;
}
createRoot(document.getElementById('root')!).render(<Fixture />);
