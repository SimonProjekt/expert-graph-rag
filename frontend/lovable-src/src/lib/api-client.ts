import type {
  ClearanceLevel,
  SearchResponse,
  ExpertsResponse,
  AskResponse,
  HealthResponse,
} from "@/types/api";

const RAW_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").trim();
const BASE = RAW_BASE === "/api" ? "" : RAW_BASE.replace(/\/+$/, "");

// ─── LRU Cache (10 entries) ─────────────────────────────────
class LRUCache<V> {
  private max: number;
  private map = new Map<string, V>();
  constructor(max = 10) {
    this.max = max;
  }
  get(key: string): V | undefined {
    const v = this.map.get(key);
    if (v !== undefined) {
      this.map.delete(key);
      this.map.set(key, v);
    }
    return v;
  }
  set(key: string, value: V) {
    if (this.map.has(key)) this.map.delete(key);
    else if (this.map.size >= this.max) {
      const first = this.map.keys().next().value;
      if (first !== undefined) this.map.delete(first);
    }
    this.map.set(key, value);
  }
}

const cache = new LRUCache<unknown>(10);

// ─── Fetch helper ────────────────────────────────────────────
async function fetchJson<T>(
  path: string,
  params: Record<string, string>,
  signal?: AbortSignal
): Promise<T> {
  const qs = new URLSearchParams(params).toString();
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const url = `${BASE}${normalizedPath}?${qs}`;
  const cacheKey = url;

  const cached = cache.get(cacheKey);
  if (cached) return cached as T;

  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  const data: T = await res.json();
  cache.set(cacheKey, data);
  return data;
}

// ─── Public API ──────────────────────────────────────────────
export function searchPapers(
  query: string,
  clearance: ClearanceLevel,
  page = 1,
  signal?: AbortSignal
) {
  return fetchJson<SearchResponse>(
    "/api/search",
    { query, clearance, page: String(page) },
    signal
  );
}

export function fetchExperts(
  query: string,
  clearance: ClearanceLevel,
  signal?: AbortSignal
) {
  return fetchJson<ExpertsResponse>(
    "/api/experts",
    { query, clearance },
    signal
  );
}

export function askQuestion(
  query: string,
  clearance: ClearanceLevel,
  signal?: AbortSignal
) {
  return fetchJson<AskResponse>("/api/ask", { query, clearance }, signal);
}

export function checkHealth(signal?: AbortSignal) {
  return fetchJson<HealthResponse>("/healthz", {}, signal);
}
