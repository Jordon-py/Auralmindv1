function normalizeApiBaseUrl(rawValue) {
  const value = String(rawValue || '')
    .trim()
    .replace(/^['"]|['"]$/g, '');

  if (!value) return '';

  if (/^https?:\/\//i.test(value)) {
    return value.replace(/\/+$/, '').replace(/\/api$/i, '');
  }

  if (/^[\w.-]+(?::\d+)?$/i.test(value)) {
    return `http://${value}`.replace(/\/+$/, '').replace(/\/api$/i, '');
  }

  return '';
}

function resolveApiBaseUrl() {
  const envBase = normalizeApiBaseUrl(import.meta.env.VITE_API_BASE_URL);
  if (envBase) return envBase;

  if (typeof window !== 'undefined') {
    const host = window.location.hostname;
    if (host === 'localhost' || host === '127.0.0.1') {
      return 'http://localhost:8000';
    }
  }

  // Production fallback for reverse-proxy / rewrite setups.
  return '';
}

const API_BASE_URL = resolveApiBaseUrl();

function buildApiUrl(path) {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${API_BASE_URL}${normalizedPath}`;
}

async function apiFetch(
  path,
  { method = 'GET', headers = {}, body = null, timeout = 30000, signal } = {}
) {
  const controller = new AbortController();
  const abortFromParent = () => controller.abort();
  if (signal) {
    if (signal.aborted) {
      controller.abort();
    } else {
      signal.addEventListener('abort', abortFromParent, { once: true });
    }
  }
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const res = await fetch(buildApiUrl(path), {
      method,
      headers,
      body,
      signal: controller.signal,
    });
    if (!res.ok) {
      const errorBody = await res.text();
      let msg = `Request failed: ${res.status}`;
      try {
        const data = JSON.parse(errorBody);
        msg = data.detail || data.message || msg;
      } catch (_) {
        msg = errorBody || msg;
      }
      throw new Error(msg);
    }
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      return await res.json();
    }
    return res;
  } catch (err) {
    if (err.name === 'AbortError') {
      if (signal?.aborted) {
        throw err;
      }
      throw new Error('Request timed out');
    }
    if (err instanceof TypeError || /failed to fetch/i.test(String(err?.message || ''))) {
      const origin = typeof window !== 'undefined' ? window.location.origin : 'the frontend origin';
      throw new Error(
        `Cannot reach API at ${API_BASE_URL || 'same-origin /api'}. Start backend and verify CORS allows ${origin}.`
      );
    }
    throw err;
  } finally {
    clearTimeout(timer);
    if (signal) {
      signal.removeEventListener('abort', abortFromParent);
    }
  }
}

export async function createJob(formData, settings) {
  const multipart = new FormData();
  for (const [key, value] of formData.entries()) {
    multipart.append(key, value);
  }
  multipart.append('settings_json', JSON.stringify(settings));
  // Uploads stream the full audio file and may hit a cold-started backend,
  // so allow well beyond the default 30s before aborting.
  return await apiFetch('/api/jobs', {
    method: 'POST',
    body: multipart,
    timeout: 10 * 60 * 1000,
  });
}

export async function fetchJobStatus(id, options = {}) {
  return await apiFetch(`/api/jobs/${id}`, options);
}

export async function fetchReport(id) {
  return await apiFetch(`/api/jobs/${id}/report`);
}

export async function fetchLogs(id, lines = 50) {
  const res = await apiFetch(`/api/jobs/${id}/logs?lines=${lines}`);
  return await res.text();
}

export async function cancelJob(id) {
  return await apiFetch(`/api/jobs/${id}/cancel`, { method: 'POST' });
}

export async function downloadOutput(id) {
  const res = await apiFetch(`/api/jobs/${id}/download`, { timeout: 5 * 60 * 1000 });
  const blob = await res.blob();
  const filename =
    res.headers.get('content-disposition')?.split('filename=')[1] || `master_${id}.wav`;
  return { blob, filename };
}
