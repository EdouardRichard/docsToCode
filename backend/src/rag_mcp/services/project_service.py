"""ProjectService: CRUD operations for projects and their knowledge scopes.

Handles atomic creation of Project + KnowledgeScope pairs (1:1 relationship).
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.models.knowledge_scope import KnowledgeScope
from rag_mcp.models.project import Project
from rag_mcp.schemas.project import ProjectCreate
from rag_mcp.utils.snowflake import generate_id


class ProjectService:
    """Service layer for project management."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_project(self, data: ProjectCreate) -> Project:
        """Create a new project with its associated knowledge scope atomically.

        Args:
            data: Project creation parameters.

        Returns:
            Created Project entity.

        Raises:
            ValueError: If alias or repo_path already exists.
        """
        now = datetime.now(timezone.utc)
        scope_id = generate_id()
        project_id = generate_id()

        # Create knowledge scope first
        scope = KnowledgeScope(
            scope_id=scope_id,
            scope_type="project",
            name=data.name,
            status="active",
            created_at=now,
            updated_at=now,
        )
        self._session.add(scope)

        # Create project linked to scope
        project = Project(
            project_id=project_id,
            name=data.name,
            alias=data.alias,
            repo_path=data.repo_path,
            knowledge_scope_id=scope_id,
            created_at=now,
            updated_at=now,
        )
        self._session.add(project)

        await self._session.flush()
        return project

    async def list_projects(self) -> list[Project]:
        """List all projects.

        Returns:
            List of Project entities.
        """
        result = await self._session.execute(
            select(Project).order_by(Project.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_project(self, project_id: int) -> Project | None:
        """Get a project by ID.

        Args:
            project_id: Snowflake ID of the project.

        Returns:
            Project entity or None if not found.
        """
        result = await self._session.execute(
            select(Project).where(Project.project_id == project_id)
        )
        return result.scalar_one_or_none()

    async def get_project_by_alias(self, alias: str) -> Project | None:
        """Get a project by alias.

        Args:
            alias: Project alias string.

        Returns:
            Project entity or None if not found.
        """
        result = await self._session.execute(
            select(Project).where(Project.alias == alias)
        )
        return result.scalar_one_or_none()

    async def delete_project(self, project_id: int) -> bool:
        """Delete a project and all its associated data.

        Deletes in FK-safe order: chunks → processing_runs → versions →
        sources → project → scope. RetrievalRuns are append-only (no FK)
        and left for TTL cleanup.

        Args:
            project_id: Snowflake ID of the project.

        Returns:
            True if deleted, False if not found.
        """
        from sqlalchemy import delete as sa_delete

        from rag_mcp.models.chunk import Chunk
        from rag_mcp.models.knowledge_source import KnowledgeSource
        from rag_mcp.models.knowledge_version import KnowledgeVersion
        from rag_mcp.models.processing_run import ProcessingRun

        project = await self.get_project(project_id)
        if project is None:
            return False

        scope_id = project.knowledge_scope_id

        # 1. Delete chunks belonging to this scope
        await self._session.execute(
            sa_delete(Chunk).where(Chunk.knowledge_scope_id == scope_id)
        )
        # 2. Delete processing runs for sources in this scope
        await self._session.execute(
            sa_delete(ProcessingRun).where(
                ProcessingRun.source_id.in_(
                    select(KnowledgeSource.source_id).where(
                        KnowledgeSource.knowledge_scope_id == scope_id
                    )
                )
            )
        )
        # 3. Delete knowledge versions for this scope
        await self._session.execute(
            sa_delete(KnowledgeVersion).where(
                KnowledgeVersion.knowledge_scope_id == scope_id
            )
        )
        # 4. Delete knowledge sources for this scope
        await self._session.execute(
            sa_delete(KnowledgeSource).where(
                KnowledgeSource.knowledge_scope_id == scope_id
            )
        )
        # 5. Delete project
        await self._session.delete(project)
        # 6. Delete knowledge scope
        scope_result = await self._session.execute(
            select(KnowledgeScope).where(KnowledgeScope.scope_id == scope_id)
        )
        scope = scope_result.scalar_one_or_none()
        if scope:
            await self._session.delete(scope)

        await self._session.flush()
        return True
