"""REST API routes for project management (FR-002, US1)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.db import get_session
from rag_mcp.schemas.project import ProjectCreate, ProjectListResponse, ProjectResponse
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
