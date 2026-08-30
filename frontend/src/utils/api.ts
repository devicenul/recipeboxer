import {
  PipelineStartRequest,
  PipelineStartResponse,
  PipelineStatus,
  SessionSummary,
  SessionDetail,
} from '../types/pipeline';
import {
  HitlPendingResponse,
  CandidateListResponse,
  HitlApproval,
  CardPairApproval,
  ApprovalPayload,
  ApprovalResponse,
} from '../types/hitl';

import { RecipeSchema } from '../types/recipe';
import {
  SourceConnectionStatus,
  SourceProviderConfig,
  ListContainersResponse,
  ListItemsResponse,
} from '../types/sources';

const BASE_URL = '';

export async function fetchJson<T>(url: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers || {});
  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json');
  }
  if (options.body && typeof options.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(`${BASE_URL}${url}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    let errorDetail = `HTTP ${response.status} ${response.statusText}`;
    try {
      const errJson = await response.json();
      if (errJson.detail) {
        errorDetail = typeof errJson.detail === 'string' ? errJson.detail : JSON.stringify(errJson.detail);
      }
    } catch {
      // Ignored
    }
    throw new Error(errorDetail);
  }

  return response.json();
}

export const api = {
  // 1. Pipeline Start & Ingestion
  async startPipeline(payload: PipelineStartRequest): Promise<PipelineStartResponse> {
    return fetchJson<PipelineStartResponse>('/api/pipeline/start', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  async uploadCards(formData: FormData): Promise<{ session_id: string; card_ids: string[]; count: number }> {
    const response = await fetch('/api/cards/upload', {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) {
      let err = `HTTP ${response.status}`;
      try {
        const j = await response.json();
        if (j.detail) err = j.detail;
      } catch {}
      throw new Error(err);
    }
    return response.json();
  },

  // 2. Status & Telemetry
  async getStatus(sessionId?: string): Promise<PipelineStatus> {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
    return fetchJson<PipelineStatus>(`/api/status${q}`);
  },

  // 3. Sessions
  async listSessions(): Promise<SessionSummary[]> {
    return fetchJson<SessionSummary[]>('/api/sessions');
  },

  async getSessionDetail(sessionId: string): Promise<SessionDetail> {
    return fetchJson<SessionDetail>(`/api/sessions/${encodeURIComponent(sessionId)}`);
  },

  async resumeSession(sessionId: string): Promise<SessionDetail> {
    return fetchJson<SessionDetail>(`/api/sessions/${encodeURIComponent(sessionId)}/resume`, {
      method: 'POST',
    });
  },

  async deleteSession(sessionId: string): Promise<{ session_id: string; deleted: boolean }> {
    return fetchJson<{ session_id: string; deleted: boolean }>(
      `/api/sessions/${encodeURIComponent(sessionId)}`,
      { method: 'DELETE' }
    );
  },

  // 4. HITL
  async getHitlPending(sessionId?: string): Promise<HitlPendingResponse> {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
    return fetchJson<HitlPendingResponse>(`/api/hitl/pending${q}`);
  },

  async getHitlCandidates(cardId: string, sessionId?: string): Promise<CandidateListResponse> {
    const sidParam = sessionId ? `&session_id=${encodeURIComponent(sessionId)}` : '';
    return fetchJson<CandidateListResponse>(
      `/api/hitl/candidates?card_id=${encodeURIComponent(cardId)}${sidParam}`
    );
  },

  async recordDecision(payload: CardPairApproval, sessionId?: string): Promise<any> {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
    return fetchJson(`/api/hitl/decision${q}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  async approveHitl(payload: ApprovalPayload): Promise<ApprovalResponse> {
    return fetchJson<ApprovalResponse>('/api/hitl/approve', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  getCardImageUrl(cardId: string, sessionId?: string): string {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
    return `/api/cards/${encodeURIComponent(cardId)}/image${q}`;
  },

  // 5. Recipe CRUD
  async listRecipes(sessionId?: string): Promise<RecipeSchema[]> {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
    return fetchJson<RecipeSchema[]>(`/api/recipes${q}`);
  },

  async getRecipe(recipeId: string, sessionId?: string): Promise<RecipeSchema> {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
    return fetchJson<RecipeSchema>(`/api/recipes/${encodeURIComponent(recipeId)}${q}`);
  },

  async createRecipe(recipe: RecipeSchema, sessionId?: string): Promise<RecipeSchema> {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
    return fetchJson<RecipeSchema>(`/api/recipes${q}`, {
      method: 'POST',
      body: JSON.stringify(recipe),
    });
  },

  async updateRecipe(recipeId: string, recipe: RecipeSchema, sessionId?: string): Promise<RecipeSchema> {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
    return fetchJson<RecipeSchema>(`/api/recipes/${encodeURIComponent(recipeId)}${q}`, {
      method: 'PUT',
      body: JSON.stringify(recipe),
    });
  },

  async deleteRecipe(recipeId: string, sessionId?: string): Promise<{ recipe_id: string; deleted: boolean }> {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
    return fetchJson<{ recipe_id: string; deleted: boolean }>(
      `/api/recipes/${encodeURIComponent(recipeId)}${q}`,
      { method: 'DELETE' }
    );
  },

  // 6. Source Providers & External Cloud Ingestion
  async listSourceProviders(): Promise<SourceConnectionStatus[]> {
    return fetchJson<SourceConnectionStatus[]>('/api/sources/providers');
  },

  async connectSourceProvider(providerId: string, config: SourceProviderConfig): Promise<SourceConnectionStatus> {
    return fetchJson<SourceConnectionStatus>(`/api/sources/${encodeURIComponent(providerId)}/connect`, {
      method: 'POST',
      body: JSON.stringify(config),
    });
  },

  async listSourceContainers(
    providerId: string,
    params?: { pageSize?: number; pageToken?: string; parentId?: string }
  ): Promise<ListContainersResponse> {
    const queryParts: string[] = [];
    if (params?.pageSize) queryParts.push(`page_size=${encodeURIComponent(params.pageSize)}`);
    if (params?.pageToken) queryParts.push(`page_token=${encodeURIComponent(params.pageToken)}`);
    if (params?.parentId) queryParts.push(`parent_id=${encodeURIComponent(params.parentId)}`);
    const q = queryParts.length > 0 ? `?${queryParts.join('&')}` : '';
    return fetchJson<ListContainersResponse>(`/api/sources/${encodeURIComponent(providerId)}/containers${q}`);
  },

  async listSourceItems(
    providerId: string,
    params?: {
      containerId?: string;
      query?: string;
      category?: string;
      pageSize?: number;
      pageToken?: string;
    }
  ): Promise<ListItemsResponse> {
    const queryParts: string[] = [];
    if (params?.containerId) queryParts.push(`container_id=${encodeURIComponent(params.containerId)}`);
    if (params?.query) queryParts.push(`query=${encodeURIComponent(params.query)}`);
    if (params?.category) queryParts.push(`category=${encodeURIComponent(params.category)}`);
    if (params?.pageSize) queryParts.push(`page_size=${encodeURIComponent(params.pageSize)}`);
    if (params?.pageToken) queryParts.push(`page_token=${encodeURIComponent(params.pageToken)}`);
    const q = queryParts.length > 0 ? `?${queryParts.join('&')}` : '';
    return fetchJson<ListItemsResponse>(`/api/sources/${encodeURIComponent(providerId)}/items${q}`);
  },

  getSourceThumbnailUrl(providerId: string, itemId: string, maxSize: number = 256): string {
    return `/api/sources/${encodeURIComponent(providerId)}/thumbnail/${encodeURIComponent(itemId)}?max_size=${maxSize}`;
  },
};

