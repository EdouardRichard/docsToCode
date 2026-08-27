import { del, get, post, uploadFile } from './client';
import type { KnowledgeSource } from '../types';

export interface KnowledgeSourceListResponse {
  items: KnowledgeSource[];
  total: number;
}

export async function listKnowledgeSources(scopeId: string): Promise<KnowledgeSource[]> {
  const response = await get<KnowledgeSourceListResponse>(
    `/api/knowledge-sources?scope_id=${encodeURIComponent(scopeId)}`,
  );
  return response.items;
}

export function uploadKnowledgeSource(scopeId: string, file: File): Promise<KnowledgeSource> {
  const formData = new FormData();
  formData.append('file', file);
  return uploadFile<KnowledgeSource>(
    `/api/knowledge-sources?scope_id=${encodeURIComponent(scopeId)}`,
    formData,
  );
}

export function reprocessSource(sourceId: string): Promise<void> {
  return post<void>(`/api/knowledge-sources/${encodeURIComponent(sourceId)}/reprocess`);
}

export function deleteSource(sourceId: string): Promise<void> {
  return del<void>(`/api/knowledge-sources/${encodeURIComponent(sourceId)}`);
}

export function clearScope(scopeId: string): Promise<void> {
  return post<void>(`/api/knowledge-sources/scopes/${encodeURIComponent(scopeId)}/clear`);
}
