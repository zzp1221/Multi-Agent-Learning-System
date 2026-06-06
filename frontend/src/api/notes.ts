import { request } from './request';

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

  analyze(id: string, force = false): Promise<NoteAnalysis> {
    return request.post<NoteAnalysis>(`/api/notes/${id}/ai/analyze`, undefined, { params: { force } });
  },

  relatedResources(id: string, topK = 6) {
    return request.get<import('./resources').ResourceSemanticSearchResponse>(`/api/notes/${id}/related-resources`, {
      params: { topK },
      dedupe: false,
    });
  },

  semantic(query: string, topK = 8): Promise<NoteSemanticSearchResponse> {
    return request.get<NoteSemanticSearchResponse>('/api/notes/search/semantic', {
      params: { query, topK },
      dedupe: false,
      retry: 0,
    });
  },
};
