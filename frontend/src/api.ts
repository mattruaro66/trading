// Cliente da API do ATLAS B3. Nenhum endpoint envia ordens.
export const API_URL: string = (import.meta.env.VITE_API_URL as string) || "http://localhost:8000";

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(API_URL + path, init);
  const text = await r.text();
  const body = text ? JSON.parse(text) : null;
  if (!r.ok) throw new ApiError(body?.detail ? String(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : r.statusText);
  return body as T;
}

export const get = <T,>(path: string) => request<T>(path);
export const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
export const postForm = <T,>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form });

export function wsUrl(path: string) {
  return API_URL.replace(/^http/, "ws") + path;
}

export interface BarDTO {
  symbol: string;
  contract: string;
  ts_open: string;
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  status: string;
  quality: string;
  quality_flags: string[];
  synthetic: boolean;
}

export interface Dataset {
  symbol: string;
  contract: string;
  bars: number;
  first_ts: string;
  last_ts: string;
  synthetic: boolean;
}

export interface Criterion {
  code: string;
  description: string;
  passed: boolean;
  value: unknown;
  threshold: unknown;
}

export interface ExplanationSection {
  title: string;
  text?: string;
  items?: unknown[];
}

export interface AlertEventDTO {
  alert_id: string;
  seq: number;
  state: string;
  state_label?: string;
  ts: string;
  reason: string;
  data: Record<string, unknown>;
  event_hash: string;
}

export interface AlertDTO {
  alert_id: string;
  created_ts: string;
  state: string;
  state_label?: string;
  payload: Record<string, any>;
  content_hash: string;
  events: AlertEventDTO[];
  integrity_ok?: boolean;
  origin?: string;
  synthetic?: boolean;
}

export const fmt = (x: unknown, nd = 2): string => {
  if (x === null || x === undefined) return "n/d";
  if (typeof x === "number") return x.toLocaleString("pt-BR", { maximumFractionDigits: nd, minimumFractionDigits: 0 });
  if (Array.isArray(x)) return x.map((v) => fmt(v, nd)).join(" – ");
  return String(x);
};
export const pct = (x: number | null | undefined) => (x === null || x === undefined ? "n/d" : (x * 100).toFixed(1).replace(".", ",") + "%");
export const ci = (x: number[] | null | undefined, asPct = false) =>
  !x ? "n/d" : asPct ? `${pct(x[0])} – ${pct(x[1])}` : `${fmt(x[0], 3)} – ${fmt(x[1], 3)}`;

/** Converte timestamp para o horário local da bolsa a partir do offset do ISO (exibição em America/Sao_Paulo). */
export function chartTime(b: { ts_open: string; time: number }): number {
  const m = b.ts_open.match(/([+-])(\d{2}):(\d{2})$/);
  if (!m) return b.time;
  const sign = m[1] === "-" ? -1 : 1;
  return b.time + sign * (parseInt(m[2]) * 3600 + parseInt(m[3]) * 60);
}
