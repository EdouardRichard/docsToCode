import { get, post, del } from './client';
import type { Project } from '../types';

export interface CreateProjectInput {
  name: string;
  alias?: string;
  repo_path?: string;
}

export function listProjects(): Promise<Project[]> {
  return get<Project[]>('/api/projects');
}

export function getProject(id: string): Promise<Project> {
  return get<Project>(`/api/projects/${encodeURIComponent(id)}`);
}

export function createProject(data: CreateProjectInput): Promise<Project> {
  return post<Project>('/api/projects', data);
}

export function deleteProject(id: string): Promise<void> {
  return del<void>(`/api/projects/${encodeURIComponent(id)}`);
}
