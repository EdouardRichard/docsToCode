import { get, post, uploadFile } from './client';
import type { KnowledgeSource } from '../types';

export function listKnowledgeSources(scopeId: string): Promise<KnowledgeSource[]> {
  return get<KnowledgeSource[]>(`/api/knowledge-scopes/${encodeURIComponent(scopeId)}/sources`);
}

export function uploadKnowledgeSource(scopeId: string, file: File): Promise<KnowledgeSource> {
  const formData = new FormData();
  formData.append('file', file);
  return uploadFile<KnowledgeSource>(
    `/api/knowledge-scopes/${encodeURIComponent(scopeId)}/sources`,
    formData,
  );
}

export function reprocessSource(sourceId: string): Promise<void> {
  return post<void>(`/api/sources/${encodeURIComponent(sourceId)}/reprocess`);
}
