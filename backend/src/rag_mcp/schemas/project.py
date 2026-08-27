"""Pydantic schemas for Project API request/response validation."""

from datetime import datetime

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    """Request body for creating a new project."""

    name: str = Field(min_length=1, max_length=200)
    alias: str | None = Field(default=None, max_length=100)
    repo_path: str | None = Field(default=None, max_length=500)


class ProjectResponse(BaseModel):
    """Response body for project endpoints."""

    project_id: str
    name: str
    alias: str | None = None
    repo_path: str | None = None
    knowledge_scope_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectListResponse(BaseModel):
    """Response body for listing projects."""

    items: list[ProjectResponse]
    total: int
