export async function phase4Api<T>(path: string, options?: RequestInit): Promise<T> {
  const token = sessionStorage.getItem("crypto_bot_admin_token");
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(options?.headers || {}) } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body as T;
}
export const post = <T,>(path: string, payload: unknown) => phase4Api<T>(path, { method: "POST", body: JSON.stringify(payload) });
export const pct = (value: number | null | undefined) => value == null ? "Insufficient Data" : `${value.toFixed(1)}%`;
