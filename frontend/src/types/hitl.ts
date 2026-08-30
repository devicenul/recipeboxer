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

export interface CorrelationProposal {
  proposal_id: string;
  ordered_card_ids: string[];
  heuristic_score: number;
  semantic_score: number;
  confidence: number;
  is_standalone: boolean;
  continuity_explanation?: string;
  recommended_action: string;
  front_card_id?: string;
  back_card_id?: string | null;
  card_ids?: string[];
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
  unassigned_cards: any[];
  total_proposals: number;
  total_unassigned: number;
}

