export enum HITLDecision {
  APPROVE = "APPROVE",
  STANDALONE = "STANDALONE",
  REJECT = "REJECT",
  REVERSE = "REVERSE",
}

export interface CandidateScoreBreakdown {
  paper_color_score?: number;
  paper_ruling_score?: number;
  ink_score?: number;
  stain_score?: number;
  boundary_text_score?: number;
  composite_heuristic_score?: number;
  semantic_score?: number;
  overall_score?: number;
}

export interface CorrelationCandidate {
  candidate_card_id: string;
  candidate_image_path?: string;
  score: number;
  heuristic_score: number;
  semantic_score: number;
  composite_score: number;
  reasoning_snippet?: string;
  score_breakdown?: CandidateScoreBreakdown;
}

export interface CandidateListResponse {
  session_id: string;
  card_id?: string;
  preceding_card_id?: string;
  front_card_id?: string;
  candidates: CorrelationCandidate[];
  total_candidates: number;
}

export interface FeatureEvidence {
  name: string;
  raw?: number | null;
  log_lr?: number | null;
  weight?: number;
  note?: string | null;
}

export interface PairEvidence {
  a: string;
  b: string;
  features?: FeatureEvidence[];
  total_log_lr?: number;
  p_link?: number | null;
  content_evidence_used?: boolean;
}

export interface SetVerification {
  anchor: string;
  continuations: string[];
  rejected: string[];
  evidence_quotes: string[];
  confidence?: number | null;
  flags?: string[];
  model?: string;
  prompt_id?: string;
  prompt_version?: string;
}

export interface CorrelationProposal {
  proposal_id: string;
  ordered_card_ids: string[];
  heuristic_score: number;
  semantic_score: number;
  confidence: number;
  p_link?: number | null; // calibrated probability
  is_standalone: boolean;
  continuity_explanation?: string;
  recommended_action: string;
  front_card_id?: string;
  back_card_id?: string | null;
  card_ids?: string[];
  semantic_verified?: boolean; // true if verified by multimodal LLM
  degraded_reason?: string | null;
  part_count?: number;
  transition_scores?: number[];
  evidence?: PairEvidence[];
  set_verification?: SetVerification;
  evidence_quotes?: string[];
  flags?: string[];
  is_low_confidence?: boolean;
}

export enum HitlItemType {
  PROPOSAL = "PROPOSAL",
  STAGE_FAILURE = "STAGE_FAILURE",
  UNASSIGNED_CARD = "UNASSIGNED_CARD",
  MANUAL_REVIEW = "MANUAL_REVIEW",
  BUDGET = "BUDGET",
}

export interface HitlItem {
  item_id: string;
  item_type?: HitlItemType | string;
  kind?: 'proposal' | 'stage_failure' | 'unassigned_card' | 'manual_review' | 'budget' | string;
  stage?: string | null;
  card_ids?: string[];
  proposal_id?: string | null;
  proposal?: CorrelationProposal | null;
  failure?: any;
  needs_review?: boolean;
  review_reasons?: string[];
  message?: string;
  created_at?: string;
  current_spend?: number;
  budget_cap?: number;
}

export interface HitlApproval {
  proposal_id?: string;
  ordered_card_ids: string[];
  is_standalone?: boolean;
  is_rejected?: boolean;
  front_card_id?: string;
  back_card_id?: string | null;
  card_ids?: string[];
  reverse_roles?: boolean;
}

export type CardPairApproval = HitlApproval;

export interface ApprovalPayload {
  session_id: string;
  approvals: HitlApproval[];
  rejected_card_ids: string[];
  continue_pipeline?: boolean;
}

export interface ApprovalResponse {
  session_id: string;
  status: string;
  approved_count: number;
  rejected_count: number;
  message: string;
}

export interface HitlPendingResponse {
  session_id: string;
  status: string;
  proposals?: CorrelationProposal[];
  proposed_pairs?: CorrelationProposal[];
  items?: HitlItem[];
  unassigned_cards: any[];
  total_proposals: number;
  total_unassigned: number;
}
