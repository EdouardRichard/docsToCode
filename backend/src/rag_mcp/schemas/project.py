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


class PublicScopeCreate(BaseModel):
    """Request body for creating a public knowledge scope.

    001 Phase 9 (FR-002 minimal public-domain management, blueprint
    §23.4.1 / §25): public knowledge uses a distinct public scope and is
    never managed through the project endpoints.
    """

    name: str = Field(min_length=1, max_length=255)


class PublicScopeResponse(BaseModel):
    """Response body for public knowledge scope endpoints."""

    scope_id: str
    scope_type: str = "public"
    name: str
    status: str
    created_at: datetime
    updated_at: datetime


class PublicScopeListResponse(BaseModel):
    """Response body for listing public knowledge scopes."""

    items: list[PublicScopeResponse]
    total: int
