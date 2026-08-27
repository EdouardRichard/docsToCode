"""Pydantic schemas for Knowledge Source API request/response validation."""

from datetime import datetime

from pydantic import BaseModel, Field


class KnowledgeSourceResponse(BaseModel):
    """Response body for knowledge source endpoints."""

    source_id: str
    knowledge_scope_id: str
    filename: str
    content_hash: str
    format: str
    size_bytes: int
    status: str
    processing_error: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeSourceListResponse(BaseModel):
    """Response body for listing knowledge sources."""

    items: list[KnowledgeSourceResponse]
    total: int


class ProcessingRunResponse(BaseModel):
    """Response body for processing run status."""

    run_id: str
    source_id: str
    run_type: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    stages: list[dict] = []

    model_config = {"from_attributes": True}
