import { request } from './request';

export type ResourceDisplayType = 'COURSE' | 'DOCUMENT' | 'VIDEO' | 'CASE' | 'NOTE';

export interface ResourceItem {
  id: string;
  title: string;
  domain: string;
  resourceType: string;
  displayType: ResourceDisplayType | string;
  difficultyLevel: string;
  sourceKind: string;
  summaryText?: string;
  tags: string[];
  sourceUrl?: string;
  sourceName?: string;
  coverUrl?: string;
  license?: string;
  copyrightStatus?: string;
  accessibilityStatus?: string;
  httpStatus?: number;
  lastCheckedAt?: string;
  qualityScore?: number;
  popularityScore?: number;
  favoriteCount?: number;
  viewCount?: number;
  likeCount?: number;
  durationMinutes?: number;
  fileSizeBytes?: number;
  favorite?: boolean;
  progress?: number;
  completed?: boolean;
  lastStudyAt?: string;
  createdAt?: string;
  updatedAt?: string;
  csCategory?: string;
  csSubcategory?: string;
  metadata?: Record<string, unknown>;
}

export interface ResourceListResponse {
  items: ResourceItem[];
  total: number;
  page: number;
  size: number;
}

export interface ResourceDetailResponse {
  resource: ResourceItem;
  ragReady: boolean;
  chunkCount: number;
  previewChunks: string[];
}

export interface ResourceUserStateResponse {
  resourceId: string;
  favorite: boolean;
  progress: number;
  completed: boolean;
  lastStudyAt?: string;
}

export interface ResourceTag {
  tag: string;
  count: number;
}

export interface ResourceStatsResponse {
  totalResources: number;
  favoriteResources: number;
  startedResources: number;
  completedResources: number;
  averageProgress: number;
  typeCounts: Record<string, number>;
  categoryCounts: Record<string, number>;
  subcategoryCounts: Record<string, number>;
  hotTags: ResourceTag[];
}

export interface ResourceSemanticHit {
  chunkId: number;
  chunkNo: number;
  similarity: number;
  content: string;
  sourceUrl?: string;
}

export interface ResourceSemanticResult {
  resourceId: string;
  resource?: ResourceItem;
  score: number;
  reason: string;
  hits: ResourceSemanticHit[];
}

export interface ResourceSemanticSearchResponse {
  query: string;
  available: boolean;
  message: string;
  results: ResourceSemanticResult[];
}

export const resourcesApi = {
  list(params: {
    keyword?: string;
    type?: string;
    domain?: string;
    subject?: string;
    category?: string;
    subcategory?: string;
    difficulty?: string;
    source?: string;
    favoriteOnly?: boolean;
    sort?: string;
    page?: number;
    size?: number;
  }): Promise<ResourceListResponse> {
    return request.get<ResourceListResponse>('/api/resources', { params, dedupe: false });
  },

  detail(id: string): Promise<ResourceDetailResponse> {
    return request.get<ResourceDetailResponse>(`/api/resources/${id}`, { dedupe: false });
  },

  favorite(id: string): Promise<ResourceUserStateResponse> {
    return request.post<ResourceUserStateResponse>(`/api/resources/${id}/favorite`);
  },

  unfavorite(id: string): Promise<ResourceUserStateResponse> {
    return request.delete<ResourceUserStateResponse>(`/api/resources/${id}/favorite`);
  },

  progress(id: string, payload: { progress: number; completed?: boolean }): Promise<ResourceUserStateResponse> {
    return request.post<ResourceUserStateResponse>(`/api/resources/${id}/progress`, payload);
  },

  recommendations(limit = 6): Promise<ResourceItem[]> {
    return request.get<ResourceItem[]>('/api/resources/recommendations', { params: { limit }, dedupe: false });
  },

  stats(): Promise<ResourceStatsResponse> {
    return request.get<ResourceStatsResponse>('/api/resources/stats', { dedupe: false });
  },

  tags(limit = 20): Promise<ResourceTag[]> {
    return request.get<ResourceTag[]>('/api/resources/tags', { params: { limit }, dedupe: false });
  },

  semantic(query: string, topK = 8): Promise<ResourceSemanticSearchResponse> {
    return request.get<ResourceSemanticSearchResponse>('/api/resources/search/semantic', {
      params: { query, topK },
      dedupe: false,
      retry: 0,
    });
  },
};
