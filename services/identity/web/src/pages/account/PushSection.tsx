/** Browser push for the platform inbox: subscribe this device (a user action asks the browser
 *  for permission — never on page load), list the devices that are subscribed, send a test push
 *  to one, remove one. What the browser cannot do is said as it is: an insecure page, a browser
 *  without Push, an iPhone that has not added Synwevo to its Home Screen, a permission the
 *  person denied. Nothing here claims delivery: the test button's success is the push service
 *  accepting the message, and the notification itself is what proves the device. */
import { useEffect, useState } from 'react';
import { ApiError, del, get, post } from '@synwevo/auth-client';
import { useI18n, useToast } from '@synwevo/ui';
import { errorText } from './common';
import './notifications.messages';

interface Device { id: string; endpoint_hash: string; user_agent?: string | null; created_at: number; last_sent_at?: number | null;
  fail_count: number; last_error?: string | null; this_device: boolean }
interface Config { enabled: boolean; public_key: string | null }
type Support = 'unsupported' | 'insecure' | 'ios-needs-install' | 'denied' | 'ready';

function support(): Support {
  if (typeof window === 'undefined') return 'unsupported';
  if (!window.isSecureContext) return 'insecure';
  const standalone = !!(window.matchMedia && window.matchMedia('(display-mode: standalone)').matches);
  const ios = /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
    return ios && !standalone ? 'ios-needs-install' : 'unsupported';
  }
  if (Notification.permission === 'denied') return 'denied';
  return 'ready';
}

function keyBytes(b64url: string): Uint8Array {
  const s = (b64url + '='.repeat((4 - (b64url.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/');
  return Uint8Array.from(atob(s), c => c.charCodeAt(0));
}

async function hash16(text: string): Promise<string> {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('').slice(0, 16);
}

function ensureManifest() {
  if (typeof document === 'undefined' || document.querySelector('link[rel="manifest"]')) return;
  const link = document.createElement('link');
  link.rel = 'manifest'; link.href = '/manifest.webmanifest';
  document.head.appendChild(link);
}

async function currentSubscription(): Promise<PushSubscription | null> {
  if (!('serviceWorker' in navigator)) return null;
  const reg = await navigator.serviceWorker.getRegistration('/');
  return reg ? reg.pushManager.getSubscription() : null;
}

function agentLabel(ua: string | null | undefined, t: (s: string) => string): string {
  const s = ua || '';
  const browser = /Edg\//.test(s) ? 'Edge' : /OPR\//.test(s) ? 'Opera' : /Firefox\//.test(s) ? 'Firefox' : /Chrome\//.test(s) ? 'Chrome' : /Safari\//.test(s) ? 'Safari' : t('浏览器');
  const os = /iPhone|iPad/.test(s) ? 'iOS' : /Android/.test(s) ? 'Android' : /Mac OS X/.test(s) ? 'macOS' : /Windows/.test(s) ? 'Windows' : /Linux/.test(s) ? 'Linux' : '';
  return os ? `${browser} · ${os}` : browser;
}

export default function PushSection() {
  const { t, locale } = useI18n();
  const toast = useToast();
  const [config, setConfig] = useState<Config | null>(null);
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [state, setState] = useState<Support>(() => support());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      const c = await get<Config>('/api/auth/push/config');
      setConfig(c);
      if (!c.enabled) { setDevices([]); return; }
      const sub = await currentSubscription().catch(() => null);
      const mine = sub ? await hash16(sub.endpoint) : '';
      const out = await get<{ items: Device[] }>(`/api/auth/push/subscriptions${mine ? `?endpoint_hash_of=${mine}` : ''}`);
      setDevices(out.items); setError(null);
    } catch (e) { setError(errorText(e, t)); }
  }
  useEffect(() => { ensureManifest(); void load(); }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  const thisDevice = devices?.find(d => d.this_device) || null;

  async function subscribe() {
    if (!config?.public_key) return;
    setBusy(true);
    try {
      ensureManifest();
      const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
      await navigator.serviceWorker.ready;
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') { setState(permission === 'denied' ? 'denied' : 'ready'); toast(t('没有获得通知权限，本设备未开启推送'), 'info'); return; }
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(config.public_key) as BufferSource });
      await post('/api/auth/push/subscriptions', { subscription: sub.toJSON() });
      toast(t('此设备已开启推送'), 'ok');
      await load();
    } catch (e) {
      // The browser could not complete the subscription (no push service reachable, a policy,
      // a private window): say so, leave the person on in-app notifications.
      toast(e instanceof ApiError ? e.message : t('此浏览器无法完成推送订阅：{reason}', { reason: e instanceof Error ? e.message : String(e) }), 'err');
    } finally { setBusy(false); }
  }

  async function remove(d: Device) {
    setBusy(true);
    try {
      if (d.this_device) { const sub = await currentSubscription().catch(() => null); if (sub) await sub.unsubscribe().catch(() => false); }
      await del(`/api/auth/push/subscriptions/${d.id}`);
      toast(t('已移除该设备的推送'), 'ok');
      await load();
    } catch (e) { toast(e instanceof ApiError ? e.message : String(e), 'err'); } finally { setBusy(false); }
  }

  async function test(d: Device) {
    setBusy(true);
    try {
      await post(`/api/auth/push/subscriptions/${d.id}/test`, {});
      toast(t('推送服务已接受测试消息；请看该设备的通知'), 'ok');
      await load();
    } catch (e) { toast(e instanceof ApiError ? e.message : String(e), 'err'); await load(); } finally { setBusy(false); }
  }

  const when = (ts?: number | null) => ts ? new Date(ts * 1000).toLocaleString(locale) : '—';
  return <div className="col gap8 push-section" data-testid="push-section">
    <h3 className="secttl sm">{t('浏览器推送')}</h3>
    <p className="tiny muted">{t('开启后，这台设备的浏览器会在通知中心有新内容时收到系统通知（须由你在此设备上开启；关闭标签页也能收到）。每类通知是否推送在上表设置；账户安全通知始终推送。')}</p>
    {error && <div className="notice err" role="alert">{error}</div>}
    {config && !config.enabled && <p className="muted" role="status">{t('此部署未启用浏览器推送。')}</p>}
    {config?.enabled && <>
      {state === 'insecure' && <p className="notice info" role="status">{t('推送只在 HTTPS 页面上可用。')}</p>}
      {state === 'unsupported' && <p className="notice info" role="status">{t('这个浏览器不支持网页推送；通知中心与邮件不受影响。')}</p>}
      {state === 'ios-needs-install' && <p className="notice info" role="status">{t('在 iPhone / iPad 上，请先在 Safari 的分享菜单里把 Synwevo「添加到主屏幕」，再从主屏幕打开并开启推送（需要 iOS 16.4 或更新）。')}</p>}
      {state === 'denied' && <p className="notice info" role="status">{t('这个浏览器已拒绝通知权限；请在浏览器的站点设置里允许通知后再试。')}</p>}
      {state === 'ready' && !thisDevice && <div><button type="button" className="btn primary sm" disabled={busy} onClick={() => void subscribe()}>{t('在此设备上开启推送')}</button></div>}
      {devices && devices.length > 0 && <div className="account-table"><table className="tbl" aria-label={t('已开启推送的设备')}>
        <thead><tr><th>{t('设备')}</th><th>{t('开启时间')}</th><th>{t('最近推送')}</th><th>{t('操作')}</th></tr></thead>
        <tbody>{devices.map(d => <tr key={d.id} data-testid="push-device">
          <td>{agentLabel(d.user_agent, t)}{d.this_device && <span className="badge info" style={{ marginLeft: 6 }}>{t('此设备')}</span>}
            {d.fail_count > 0 && <div className="tiny muted">{t('最近 {n} 次推送失败', { n: String(d.fail_count) })}</div>}</td>
          <td>{when(d.created_at)}</td>
          <td>{when(d.last_sent_at)}</td>
          <td className="row gap4"><button type="button" className="btn sm" disabled={busy} onClick={() => void test(d)}>{t('发送测试')}</button>
            <button type="button" className="btn sm" disabled={busy} onClick={() => void remove(d)}>{t('移除')}</button></td>
        </tr>)}</tbody>
      </table></div>}
      {devices && devices.length === 0 && state === 'ready' && <p className="tiny muted">{t('还没有设备开启推送。')}</p>}
    </>}
  </div>;
}
