// ─── Clearance ───────────────────────────────────────────────
export type ClearanceLevel = "PUBLIC" | "INTERNAL" | "CONFIDENTIAL";

// ─── Search ──────────────────────────────────────────────────
export interface ScoreBreakdown {
  semantic_relevance: number;
  graph_authority: number;
  graph_centrality: number;
}

export interface PaperResult {
  paper_id: string;
  title: string;
  published_date: string;
  snippet: string;
  authors: string[];
  topics: string[];
  relevance_score: number;
  semantic_relevance_score: number;
  source: string;
  graph_hop_distance: number;
  score_breakdown: ScoreBreakdown;
  why_matched: string;
  graph_path: string[];
}

export interface SearchResponse {
  results: PaperResult[];
  hidden_count: number;
  redacted_count: number;
  result_count: number;
  took_ms: number;
  live_fetch: boolean;
}

// ─── Experts ─────────────────────────────────────────────────
export interface Expert {
  expert_id: string;
  name: string;
  affiliation: string;
  score: number;
  score_breakdown: ScoreBreakdown;
  explanation: string;
  top_papers: string[];
}

export interface ExpertsResponse {
  experts: Expert[];
  took_ms: number;
}

// ─── Ask ─────────────────────────────────────────────────────
export interface Citation {
  paper_id: string;
  title: string;
  snippet: string;
}

export interface AskResponse {
  answer: string;
  citations: Citation[];
  recommended_experts: Expert[];
  took_ms: number;
}

// ─── Health ──────────────────────────────────────────────────
export interface HealthResponse {
  status: string;
  llm_available: boolean;
  openalex_available: boolean;
  demo_mode: boolean;
}
