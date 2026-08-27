"""Shared pytest fixtures for RAG MCP tests."""

import os
from typing import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rag_mcp.config import get_settings
from rag_mcp.models import Base

# Tests exercise the upload endpoint but do not depend on the asynchronous
# ingestion pipeline (which loads the real bge-m3 model and runs against live
# Qdrant). Disable background ingestion so uploads stay in 'uploaded' status,
# keeping tests deterministic and free of un-awaited background tasks.
os.environ.setdefault("INGESTION_BACKGROUND", "false")


@pytest_asyncio.fixture
async def engine():
    """Create async engine connected to database."""
    settings = get_settings()
    eng = create_async_engine(settings.database_url, echo=False)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test session."""
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def test_client(engine):
    """Create an async test client with DB session dependency override."""
    from httpx import ASGITransport, AsyncClient

    from rag_mcp.api.projects import get_session as projects_get_session
    from rag_mcp.api.knowledge_sources import get_session as ks_get_session
    from rag_mcp.server import app

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[projects_get_session] = _get_session
    app.dependency_overrides[ks_get_session] = _get_session

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
def sample_markdown():
    """Sample Markdown content for parser tests."""
    return """# 项目概述

这是一个示例项目的文档。

## 安装指南

### 环境要求

- Python 3.12+
- PostgreSQL 16

### 安装步骤

1. 克隆仓库
2. 安装依赖：`pip install -r requirements.txt`
3. 配置数据库连接

## API 配置

数据库密码: password=MySecret123
API密钥: api_key=sk-abc123def456ghi789jkl012mno345
Token: bearer_token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U

## 架构说明

系统采用前后端分离架构。
"""


@pytest_asyncio.fixture
def sample_java():
    """Sample Java source code for parser tests."""
    return '''package com.example.service;

import java.util.List;
import java.util.Optional;

/**
 * 用户服务类，处理用户相关业务逻辑。
 */
public class UserService {

    private static final String DB_PASSWORD = "SuperSecret456";
    private final UserRepository repository;

    public UserService(UserRepository repository) {
        this.repository = repository;
    }

    /**
     * 根据ID查找用户。
     * @param id 用户ID
     * @return 用户实体
     */
    public Optional<User> findById(Long id) {
        return repository.findById(id);
    }

    /**
     * 获取所有活跃用户。
     */
    public List<User> getActiveUsers() {
        return repository.findByStatus("active");
    }

    private void validateToken(String token) {
        // Token validation logic
        if (token == null || token.isEmpty()) {
            throw new IllegalArgumentException("Token required");
        }
    }
}
'''
