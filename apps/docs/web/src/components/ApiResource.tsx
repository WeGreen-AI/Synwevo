import { useEffect, useRef, useState, type ImgHTMLAttributes, type ReactNode } from 'react';
import { useI18n, useToast } from '@synwevo/ui';
import { getSessionGeneration, assertSessionGeneration } from '@synwevo/auth-client/api';
import { errorText } from '../lib/perm';
import { downloadResource, readResourceBlob } from '../lib/resources';

export function DownloadLink({ href, children, className, title }: { href: string; children: ReactNode; className?: string; title?: string }) {
  const { t } = useI18n();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const active = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const generation = getSessionGeneration();
  useEffect(() => {
    active.current = false; setBusy(false);
    return () => { controller.current?.abort(); controller.current = null; active.current = false; };
  }, [href, generation]);
  return <button type="button" className={className || 'btn ghost xs'} title={title} disabled={busy} aria-busy={busy}
    onClick={async () => {
      if (active.current) return;
      active.current = true; setBusy(true);
      const request = new AbortController(); controller.current = request;
      try { await downloadResource(href, request.signal); }
      catch (error) { if (!request.signal.aborted && !(error instanceof Error && error.name === 'AbortError')) toast(errorText(error), 'err'); }
      finally { if (controller.current === request) { controller.current = null; active.current = false; setBusy(false); } }
    }}>{busy ? t('正在下载…') : children}</button>;
}

/** Fetch only visible page images and release blobs when a page/file leaves the view. */
export function ApiImage({ src, loading, ...props }: ImgHTMLAttributes<HTMLImageElement> & { src: string }) {
  const { t } = useI18n();
  const generation = getSessionGeneration();
  const ref = useRef<HTMLImageElement>(null);
  const [loaded, setLoaded] = useState<{ src: string; url: string; generation: number }>();
  const [failure, setFailure] = useState<{ error: unknown; generation: number }>();
  const [visible, setVisible] = useState(loading !== 'lazy');
  useEffect(() => {
    if (visible || !ref.current) return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) setVisible(true);
    }, { rootMargin: '600px' });
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [visible]);
  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    let url: string | undefined;
    setFailure(undefined);
    readResourceBlob(src, controller.signal, generation).then(({ blob }) => {
      assertSessionGeneration(generation, controller.signal);
      url = URL.createObjectURL(blob); setLoaded({ src, url, generation });
    }).catch(error => {
      if (!controller.signal.aborted && generation === getSessionGeneration()
          && !(error instanceof Error && error.name === 'AbortError')) setFailure({ error, generation });
    });
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [src, visible, generation]);
  return <><img {...props} ref={ref} src={loaded?.src === src && loaded.generation === generation ? loaded.url : undefined} />
    {failure?.generation === generation && <span className="notice err small" role="status">{t('图片加载失败')}：{errorText(failure.error)}</span>}</>;
}
