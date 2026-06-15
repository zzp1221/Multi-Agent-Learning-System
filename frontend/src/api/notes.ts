import { request } from './request';
import type { ResourceSemanticSearchResponse } from './resources';

const NOTE_ANALYSIS_CACHE_KEY = 'notebook_note_analysis_cache';
const NOTE_RELATED_RESOURCES_CACHE_KEY = 'notebook_related_resources_cache';
const NOTE_ANALYSIS_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const NOTE_RELATED_RESOURCES_TTL_MS = 30 * 60 * 1000;

export interface NoteTag {
  id: string;
  name: string;
  color: string;
  count: number;
}

export interface NoteFolder {
  id: string;
  parentId?: string | null;
  name: string;
  sortOrder: number;
  noteCount: number;
  createdAt?: string;
  updatedAt?: string;
}

export interface NoteListItem {
  id: string;
  folderId?: string | null;
  title: string;
  preview: string;
  tags: NoteTag[];
  wordCount: number;
  readingMinutes: number;
  lastSavedAt?: string;
  updatedAt?: string;
  ragIndexed: boolean;
}

export interface NoteDetail extends NoteListItem {
  markdownContent: string;
  plainText: string;
  contentHash: string;
  createdAt?: string;
  ragResourceId?: string | null;
}

export interface NoteListResponse {
  items: NoteListItem[];
  total: number;
  page: number;
  size: number;
}

export interface NoteVersion {
  id: string;
  versionNo: number;
  title: string;
  markdownContent: string;
  plainText: string;
  contentHash: string;
  changeSummary: string;
  createdAt?: string;
}

export interface NoteTodo {
  title: string;
  priority: 'HIGH' | 'MEDIUM' | 'LOW' | string;
  completed: boolean;
}

export interface NoteAnalysis {
  inputHash: string;
  summary: string;
  keywords: string[];
  todos: NoteTodo[];
  provider?: string;
  model?: string;
  generatedAt?: string;
  fromCache: boolean;
}

export interface NoteSemanticHit {
  chunkId: number;
  chunkNo: number;
  similarity: number;
  content: string;
}

export interface NoteSemanticResult {
  note: NoteListItem;
  score: number;
  reason: string;
  hits: NoteSemanticHit[];
}

export interface NoteSemanticSearchResponse {
  query: string;
  available: boolean;
  message: string;
  results: NoteSemanticResult[];
}

export const notesApi = {
  list(params: { keyword?: string; folderId?: string; tag?: string; page?: number; size?: number }): Promise<NoteListResponse> {
    return request.get<NoteListResponse>('/api/notes', { params, dedupe: false });
  },

  create(payload: { title?: string; markdownContent?: string; folderId?: string | null; tags?: string[] }): Promise<NoteDetail> {
    return request.post<NoteDetail>('/api/notes', payload);
  },

  detail(id: string): Promise<NoteDetail> {
    return request.get<NoteDetail>(`/api/notes/${id}`, { dedupe: false });
  },

  update(id: string, payload: { title?: string; markdownContent?: string; folderId?: string | null; clearFolder?: boolean; tags?: string[] }): Promise<NoteDetail> {
    return request.put<NoteDetail>(`/api/notes/${id}`, payload);
  },

  delete(id: string): Promise<void> {
    return request.delete<void>(`/api/notes/${id}`);
  },

  folders(): Promise<NoteFolder[]> {
    return request.get<NoteFolder[]>('/api/notes/folders', { dedupe: false });
  },

  createFolder(payload: { name: string; parentId?: string | null; sortOrder?: number }): Promise<NoteFolder> {
    return request.post<NoteFolder>('/api/notes/folders', payload);
  },

  updateFolder(id: string, payload: { name?: string; parentId?: string | null; sortOrder?: number }): Promise<NoteFolder> {
    return request.put<NoteFolder>(`/api/notes/folders/${id}`, payload);
  },

  deleteFolder(id: string): Promise<void> {
    return request.delete<void>(`/api/notes/folders/${id}`);
  },

  tags(): Promise<NoteTag[]> {
    return request.get<NoteTag[]>('/api/notes/tags', { dedupe: false });
  },

  updateTags(id: string, tags: string[]): Promise<NoteDetail> {
    return request.put<NoteDetail>(`/api/notes/${id}/tags`, { tags });
  },

  versions(id: string): Promise<NoteVersion[]> {
    return request.get<NoteVersion[]>(`/api/notes/${id}/versions`, { dedupe: false });
  },

  restoreVersion(noteId: string, versionId: string): Promise<NoteDetail> {
    return request.post<NoteDetail>(`/api/notes/${noteId}/versions/${versionId}/restore`);
  },

  async analyze(id: string, force = false, inputHash?: string): Promise<NoteAnalysis> {
    if (!force) {
      const cached = readAnalysisCache(id, inputHash);
      if (cached) {
        return cached;
      }
    }
    const analysis = await request.post<NoteAnalysis>(`/api/notes/${id}/ai/analyze`, undefined, { params: { force } });
    writeAnalysisCache(id, analysis);
    return analysis;
  },

  async relatedResources(id: string, topK = 6): Promise<ResourceSemanticSearchResponse> {
    const cached = readRelatedResourcesCache(id, topK);
    if (cached) {
      return cached;
    }
    const response = await request.get<ResourceSemanticSearchResponse>(`/api/notes/${id}/related-resources`, {
      params: { topK },
      dedupe: false,
    });
    writeRelatedResourcesCache(id, topK, response);
    return response;
  },

  semantic(query: string, topK = 8): Promise<NoteSemanticSearchResponse> {
    return request.get<NoteSemanticSearchResponse>('/api/notes/search/semantic', {
      params: { query, topK },
      dedupe: false,
      retry: 0,
    });
  },
};

function readAnalysisCache(noteId: string, inputHash?: string): NoteAnalysis | null {
  const cache = readLocalRecord<{
    savedAt: number;
    analysis: NoteAnalysis;
  }>(NOTE_ANALYSIS_CACHE_KEY);
  if (inputHash) {
    return readFreshAnalysis(cache[analysisCacheId(noteId, inputHash)]);
  }
  return Object.entries(cache)
    .find(([key, entry]) => key.startsWith(`${noteId}:`) && readFreshAnalysis(entry))?.[1]
    ?.analysis ?? null;
}

export function readLocalNoteAnalysis(noteId: string, inputHash?: string): NoteAnalysis | null {
  return readAnalysisCache(noteId, inputHash);
}

function writeAnalysisCache(noteId: string, analysis: NoteAnalysis): void {
  const cache = readLocalRecord<{
    savedAt: number;
    analysis: NoteAnalysis;
  }>(NOTE_ANALYSIS_CACHE_KEY);
  Object.keys(cache)
    .filter((key) => key.startsWith(`${noteId}:`))
    .forEach((key) => delete cache[key]);
  cache[analysisCacheId(noteId, analysis.inputHash)] = {
    savedAt: Date.now(),
    analysis: { ...analysis, fromCache: true },
  };
  writeLocalRecord(NOTE_ANALYSIS_CACHE_KEY, cache);
}

function readFreshAnalysis(entry?: { savedAt: number; analysis: NoteAnalysis }): NoteAnalysis | null {
  if (!entry || Date.now() - entry.savedAt > NOTE_ANALYSIS_TTL_MS) {
    return null;
  }
  return entry.analysis;
}

function analysisCacheId(noteId: string, inputHash: string): string {
  return `${noteId}:${inputHash || 'unknown'}`;
}

function readRelatedResourcesCache(
  noteId: string,
  topK: number,
): ResourceSemanticSearchResponse | null {
  const cache = readLocalRecord<{
    savedAt: number;
    response: ResourceSemanticSearchResponse;
  }>(NOTE_RELATED_RESOURCES_CACHE_KEY);
  const cached = cache[relatedResourcesCacheId(noteId, topK)];
  if (!cached || Date.now() - cached.savedAt > NOTE_RELATED_RESOURCES_TTL_MS) {
    return null;
  }
  return cached.response;
}

function writeRelatedResourcesCache(
  noteId: string,
  topK: number,
  response: ResourceSemanticSearchResponse,
): void {
  const cache = readLocalRecord<{
    savedAt: number;
    response: ResourceSemanticSearchResponse;
  }>(NOTE_RELATED_RESOURCES_CACHE_KEY);
  cache[relatedResourcesCacheId(noteId, topK)] = {
    savedAt: Date.now(),
    response,
  };
  writeLocalRecord(NOTE_RELATED_RESOURCES_CACHE_KEY, cache);
}

function relatedResourcesCacheId(noteId: string, topK: number): string {
  return `${noteId}:${topK}`;
}

function readLocalRecord<T>(key: string): Record<string, T> {
  if (typeof window === 'undefined') {
    return {};
  }
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || '{}') as Record<string, T>;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return {};
    }
    return parsed;
  } catch {
    window.localStorage.removeItem(key);
    return {};
  }
}

function writeLocalRecord<T>(key: string, value: Record<string, T>): void {
  if (typeof window === 'undefined') {
    return;
  }
  window.localStorage.setItem(key, JSON.stringify(value));
}
