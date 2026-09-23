export type View = "overview" | "client" | "graph" | "exports";
export type Role = "consolidator" | "transit" | "distributor" | "terminal" | "coordinator" | "peripheral";
export interface Client {
  gid: string; role: Role; role_score: number; priority_score: number; cluster_id: number;
  depth: number; is_seed: boolean; truncated_by_depth: boolean; in_deg: number; out_deg: number;
  in_kzt: number; out_kzt: number; in_tx: number; out_tx: number; evidence: string; why: string;
  observation_note: string; alternative_role: string; alternative_role_score: number; seed_reach: number; active_days: number;
  pagerank: number; betweenness: number; pass_through: number | null; intercluster_degree: number;
  contribution_seed_convergence: number; contribution_observed_flow: number;
  contribution_brokerage: number; contribution_fan_in: number;
  temporal_matched_kzt: number; temporal_share: number;
}
export interface Edge { src: string; dst: string; sum_kzt: number; n_tx: number }
export interface Transaction { src: string; dst: string; sum_kzt: number; date: string }
export interface ClientDetails {
  client: Client; incoming: Edge[]; outgoing: Edge[];
  daily: { date: string; in_kzt: number; out_kzt: number }[];
  transactions: Transaction[];
}
export interface Overview {
  summary: { n_nodes: number; n_edges: number; n_transactions: number; n_seed: number; n_clusters: number; boundary_count: number; sum_kzt: number; date_min: string | null; date_max: string | null };
  roles: { role: Role; label: string; color: string; count: number }[];
  clusters: { cluster_id: number; n_nodes: number; n_seed: number; sum_kzt_internal: number; hypothesis: string }[];
  metadata: { input_fingerprint?: string; timings?: { total_seconds?: number }; temporal?: { window_days?: number }; [key: string]: unknown };
}
export interface ClientList { items: Client[]; total: number; role_counts?: Record<string, number> }
export interface Filters { roles: string[]; clusters: string[]; seed: "all" | "seed" | "nonseed"; boundary: "all" | "boundary" | "internal" }
export const emptyFilters: Filters = { roles: [], clusters: [], seed: "all", boundary: "all" };
export const labels: Record<string, string> = { consolidator: "Консолидатор", transit: "Транзит", distributor: "Распределитель", terminal: "Конечный получатель", coordinator: "Координатор", peripheral: "Периферия" };
export const colors: Record<string, string> = { consolidator: "#83b9e8", transit: "#87c8c9", distributor: "#d7b786", terminal: "#b3a4d7", coordinator: "#cf99a7", peripheral: "#bec7cf" };
export function filterParams(filters: Filters) {
  const query = new URLSearchParams();
  if (filters.roles.length) query.set("roles", filters.roles.join(","));
  if (filters.clusters.length) query.set("clusters", filters.clusters.join(","));
  query.set("seed", filters.seed); query.set("boundary", filters.boundary);
  return query;
}
export const formatNumber = (n: number, digits = 0) => new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(n);
export const formatMoney = (n: number) => n >= 1e9 ? `${formatNumber(n / 1e9, 2)} млрд` : n >= 1e6 ? `${formatNumber(n / 1e6, 2)} млн` : formatNumber(n, 0);
export const shortDate = (value: string | null) => value ? new Date(`${value.slice(0, 10)}T12:00:00`).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" }) : "—";
export async function request<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal, cache: "no-store" });
  if (!response.ok) {
    let message = response.status === 404 ? "Клиент не найден в предоставленной выборке." : "Не удалось получить данные. Проверьте, что аналитический сервер запущен.";
    try { const data = await response.json(); if (typeof data.detail === "string") message = data.detail; } catch { /* A proxy error may not return JSON. */ }
    throw new Error(message);
  }
  return response.json();
}
