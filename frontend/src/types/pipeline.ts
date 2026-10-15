export enum PipelineState {
  IDLE = "IDLE",
  SCANNING = "SCANNING",
  FINGERPRINTING = "FINGERPRINTING",
  CORRELATING = "CORRELATING",
  HITL_PENDING = "HITL_PENDING",
  EXTRACTING = "EXTRACTING",
  EXPORTING = "EXPORTING",
  COMPLETED = "COMPLETED",
  FAILED = "FAILED",
  CANCELLED = "CANCELLED",
  BUDGET_EXCEEDED = "BUDGET_EXCEEDED",
}

export enum IngestionMode {
  PARALLEL = "parallel",
  SLOW_BATCH = "slow-batch",
}

export interface ProgressMetrics {
  total_scans: number;
  skipped_cards?: number;
  newly_processed_cards?: number;
  fingerprinted: number;
  proposals_generated: number;
  approved_recipes?: number;
  approved_pairs?: number;
  recipes_extracted: number;
  baseline_recipes_count?: number;
  current_stage?: string;
  percent_complete: number;
}


export interface TokenUsage {
  prompt_tokens: number;
  candidates_tokens: number;
  thinking_tokens: number;
  total_tokens: number;
}

export interface PipelineStatus {
  session_id: string;
  status: PipelineState | string;
  mode?: IngestionMode;
  progress: ProgressMetrics;
  token_usage: TokenUsage;
  elapsed_seconds: number;
  csv_output_path?: string | null;
  error?: string | null;
  analysis_blocked_reason?: string | null;
  onboarding_hint?: string | null;
  spend_usd?: number;
  estimated_cost_usd?: number | null;
  failures?: any[];
  failures_count?: number;
  [key: string]: any;
}

export interface PipelineStartRequest {
  scans_dir?: string;
  directory_path?: string;
  folder_path?: string;
  baseline_project_path?: string;
  baseline_session_id?: string;
  default_tags?: string[];
  source_provider?: string;
  source_container_id?: string;
  source_item_ids?: string[];
  source_config?: Record<string, any>;
  mode?: IngestionMode | string;
  max_concurrency?: number;
  slow_batch_size?: number;
  slow_batch_interval_seconds?: number;
  model?: string;
  thinking_mode?: boolean;
  thinking_budget_tokens?: number;
}

export interface PipelineStartResponse {
  session_id: string;
  status: PipelineState | string;
  total_scans_found: number;
  mode: IngestionMode | string;
  message: string;
}

export interface SessionSummary {
  session_id: string;
  folder_path?: string | null;
  status: PipelineState | string;
  created_at: string;
  card_count: number;
  skipped_cards?: number;
  recipe_count: number;
  mode?: string;
  baseline_project_path?: string | null;
  baseline_session_id?: string | null;
  csv_output_path?: string | null;
  spend_usd?: number;
  estimated_cost_usd?: number | null;
  analysis_blocked_reason?: string | null;
  onboarding_hint?: string | null;
  [key: string]: any;
}

export interface SessionDetail {
  session_id: string;
  folder_path?: string | null;
  status: PipelineState | string;
  mode: string;
  config: Record<string, any>;
  progress: ProgressMetrics;
  token_usage: TokenUsage;
  elapsed_seconds: number;
  created_at: string;
  updated_at: string;
  card_count?: number;
  skipped_cards?: number;
  recipe_count?: number;
  baseline_recipes_count?: number;
  baseline_project_path?: string | null;
  baseline_session_id?: string | null;
  cards: any[];
  proposals?: any[];
  recipes: any[];
  csv_output_path?: string | null;
  error?: string | null;
  spend_usd?: number;
  estimated_cost_usd?: number | null;
  spend_breakdown?: Record<string, any>;
  analysis_blocked_reason?: string | null;
  onboarding_hint?: string | null;
  failures?: any[];
  [key: string]: any;
}

// ---------------------------------------------------------------------------
// Settings & Telemetry Schemas
// ---------------------------------------------------------------------------

export interface SettingsResponse {
  quality_profile: 'economy' | 'balanced' | 'best' | string;
  stage_models: Record<string, string>;
  spend_cap_usd: number | null;
  monthly_cap_usd: number | null;
  rpm_limit: number;
  extra_effort: boolean;
  embeddings_enabled: boolean;
  has_api_key: boolean;
  key_source: string;
}

export interface SettingsUpdateRequest {
  quality_profile?: string;
  stage_models?: Record<string, string>;
  spend_cap_usd?: number | null;
  monthly_cap_usd?: number | null;
  rpm_limit?: number;
  extra_effort?: boolean;
  embeddings_enabled?: boolean;
}

export interface ApiKeyRequest {
  api_key: string;
}

export interface ApiKeyResponse {
  success: boolean;
  has_api_key: boolean;
  key_source: string;
  message: string;
}

export interface ApiKeyDeleteResponse {
  success: boolean;
  has_api_key: boolean;
  key_source: string;
  message: string;
}

export interface CostEstimateResponse {
  low: number | null;
  expected: number | null;
  high: number | null;
  assumptions: Record<string, any>;
}

export interface PrivacyNoticeResponse {
  notice: string;
  terms_url: string;
  privacy_policy_url: string;
  paid_vs_free_summary: string;
}

export interface SpendEventPayload {
  session_id: string;
  spend_usd: number;
  snapshot?: {
    total_usd?: number;
    total_calls?: number;
    prompt_tokens?: number;
    candidates_tokens?: number;
    thoughts_tokens?: number;
    stages?: Record<string, any>;
    [key: string]: any;
  };
  total_calls?: number;
  total_tokens?: number;
  [key: string]: any;
}
