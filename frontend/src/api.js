// Thin client for the knowledge-keeper Python API (src/knowledge_keeper/web/server.py).
// Every state-changing request carries X-KK-Client, which the server requires so other
// websites open in your browser can't drive the app.

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function api(path, { method = "GET", body, form } = {}) {
  const headers = { "X-KK-Client": "1" };
  let payload;
  if (form) {
    payload = form;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, { method, headers, body: payload });
  } catch {
    throw new ApiError("Can't reach Knowledge Keeper. Is the app still running?", 0);
  }
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const detail = data && typeof data === "object" ? data.detail : data;
    throw new ApiError(typeof detail === "string" ? detail : `Request failed (${res.status})`, res.status);
  }
  return data;
}

export const get = (path) => api(path);
export const post = (path, body = {}) => api(path, { method: "POST", body });
export const put = (path, body = {}) => api(path, { method: "PUT", body });
export const del = (path) => api(path, { method: "DELETE" });

export function upload(files) {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  return api("/api/documents/upload", { method: "POST", form });
}
