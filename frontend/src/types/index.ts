export interface Project {
  project_id: string;
  name: string;
  alias?: string;
  repo_path?: string;
  knowledge_scope_id: string;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeSource {
  source_id: string;
  knowledge_scope_id?: string;
  filename: string;
  content_hash: string;
  format: 'markdown' | 'java';
  size_bytes: number;
  status: 'uploaded' | 'processing' | 'published' | 'failed' | 'deleted';
  processing_error?: string;
  created_at: string;
  updated_at: string;
}

export interface SSEEvent {
  event: 'processing_progress' | 'publish_progress' | 'delete_progress' | 'error';
  data: {
    source_id?: string;
    run_id?: string;
    stage?: string;
    progress?: number;
    message?: string;
  };
}
