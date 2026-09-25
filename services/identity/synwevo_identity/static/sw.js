/* Synwevo Account service worker: Web Push only. It caches nothing and intercepts no request,
 * so the shell's own loading and sign-in behaviour are untouched.
 *
 * push                    → show the inbox item the server rendered (title, body, link)
 * notificationclick       → focus an open Synwevo tab on the link, or open one
 * pushsubscriptionchange  → the browser rotated the subscription: re-subscribe with the same
 *                           application server key and tell the account service
 */
'use strict';

self.addEventListener('install', () => { self.skipWaiting(); });
self.addEventListener('activate', (event) => { event.waitUntil(self.clients.claim()); });

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { title: 'Synwevo', body: event.data ? event.data.text() : '' }; }
  const title = data.title || 'Synwevo';
  const options = {
    body: data.body || '',
    tag: data.id ? `synwevo-${data.id}` : undefined,   // the same item twice replaces, never stacks
    renotify: false,
    data: { href: data.href || null, id: data.id || null },
    icon: '/favicon.ico',
    badge: '/favicon.ico',
    timestamp: data.created_at ? Math.round(data.created_at * 1000) : undefined,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const href = event.notification.data && event.notification.data.href;
  if (!href) return;
  event.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of all) {
      if (client.url === href && 'focus' in client) return client.focus();
    }
    if (self.clients.openWindow) return self.clients.openWindow(href);
    return undefined;
  })());
});

self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil((async () => {
    const old = event.oldSubscription;
    const key = old && old.options && old.options.applicationServerKey;
    if (!key) return;
    const fresh = await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
    const csrf = (await self.cookieStore?.get?.('dw_csrf'))?.value;
    await fetch('/api/auth/push/subscriptions', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-Synwevo-CSRF': csrf } : {}) },
      body: JSON.stringify({ subscription: fresh.toJSON() }),
    });
  })());
});
