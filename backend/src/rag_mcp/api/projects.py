"""REST API routes for project management (FR-002, US1)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.db import get_session
from rag_mcp.schemas.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    PublicScopeCreate,
    PublicScopeListResponse,
    PublicScopeResponse,
)
from rag_mcp.services.project_service import ProjectService

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    data: ProjectCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create a new project with its associated knowledge scope."""
    service = ProjectService(session)
    project = await service.create_project(data)
    await session.commit()
    return ProjectResponse(
        project_id=str(project.project_id),
        name=project.name,
        alias=project.alias,
        repo_path=project.repo_path,
        knowledge_scope_id=str(project.knowledge_scope_id),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    session: AsyncSession = Depends(get_session),
):
    """List all projects."""
    service = ProjectService(session)
    projects = await service.list_projects()
    items = [
        ProjectResponse(
            project_id=str(p.project_id),
            name=p.name,
            alias=p.alias,
            repo_path=p.repo_path,
            knowledge_scope_id=str(p.knowledge_scope_id),
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p in projects
    ]
    return ProjectListResponse(items=items, total=len(items))


@router.post("/public-scopes", response_model=PublicScopeResponse, status_code=201)
async def create_public_scope(
    data: PublicScopeCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create a public knowledge scope (001 Phase 9, FR-002 minimal
    public-domain management; blueprint §23.4.1 / §25)."""
    service = ProjectService(session)
    try:
        scope = await service.create_public_scope(data.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await session.commit()
    return PublicScopeResponse(
        scope_id=str(scope.scope_id),
        scope_type=scope.scope_type,
        name=scope.name,
        status=scope.status,
        created_at=scope.created_at,
        updated_at=scope.updated_at,
    )


@router.get("/public-scopes", response_model=PublicScopeListResponse)
async def list_public_scopes(
    session: AsyncSession = Depends(get_session),
):
    """List public knowledge scopes (project scopes are listed via GET /api/projects)."""
    service = ProjectService(session)
    scopes = await service.list_public_scopes()
    items = [
        PublicScopeResponse(
            scope_id=str(s.scope_id),
            scope_type=s.scope_type,
            name=s.name,
            status=s.status,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in scopes
    ]
    return PublicScopeListResponse(items=items, total=len(items))


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Get a project by ID."""
    service = ProjectService(session)
    project = await service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(
        project_id=str(project.project_id),
        name=project.name,
        alias=project.alias,
        repo_path=project.repo_path,
        knowledge_scope_id=str(project.knowledge_scope_id),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Delete a project and its knowledge scope."""
    service = ProjectService(session)
    deleted = await service.delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    await session.commit()
