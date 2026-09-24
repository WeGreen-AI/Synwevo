import { ApiError, authHeaders, notifySessionExpired, getSessionGeneration, assertSessionGeneration } from '@synwevo/auth-client/api';

/** Binary resources need the same tab-bound workspace context as JSON requests.
 * Never put tenancy into a query parameter or navigate directly to an API download. */
export async function readResource(path: string, signal?: AbortSignal, generation = getSessionGeneration()): Promise<Response> {
  if (!path.startsWith('/api/')) throw new Error('Only local API resources are supported');
  assertSessionGeneration(generation, signal);
  let response: Response;
  try { response = await fetch(path, { headers: authHeaders('GET', false), signal }); }
  catch (error: any) {
    assertSessionGeneration(generation, signal);
    if (error?.name === 'AbortError') throw error;
    throw new ApiError(0, 'network', '');
  }
  assertSessionGeneration(generation, signal);
  if (!response.ok || (response.headers.get('Content-Type')?.includes('text/html') && !response.headers.has('X-Request-ID'))) {
    const body = await response.json().catch(() => null);
    assertSessionGeneration(generation, signal);
    const error = new ApiError(response.status, body?.error || 'http_error', body?.message || '', body?.detail);
    error.requestId = body?.request_id || response.headers.get('X-Request-ID') || undefined;
    notifySessionExpired(error.status, error.code, response.headers, error.requestId, generation);
    throw error;
  }
  return response;
}

/** Keep the request's binding through body decoding, which can finish after headers arrive.
 * Callers check the same generation once more before creating a URL or updating the UI. */
export async function readResourceBlob(path: string, signal?: AbortSignal, generation = getSessionGeneration()) {
  const response = await readResource(path, signal, generation);
  assertSessionGeneration(generation, signal);
  try {
    const blob = await response.blob();
    assertSessionGeneration(generation, signal);
    return { response, blob };
  } catch (error) {
    assertSessionGeneration(generation, signal);
    throw error;
  }
}

export function downloadName(disposition: string | null, fallback = 'download'): string {
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition || '')?.[1];
  let name = /filename="([^"]+)"|filename=([^;]+)/i.exec(disposition || '')?.slice(1).find(Boolean) || fallback;
  if (encoded) { try { name = decodeURIComponent(encoded); } catch { /* malformed server header */ } }
  return name.replace(/[\\/\u0000-\u001f\u007f]/g, '_');
}

export async function downloadResource(path: string, signal?: AbortSignal): Promise<void> {
  const generation = getSessionGeneration();
  const { response, blob } = await readResourceBlob(path, signal, generation);
  assertSessionGeneration(generation, signal);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = downloadName(response.headers.get('Content-Disposition'));
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  // Firefox needs the object URL to survive until the download has been dispatched.
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
