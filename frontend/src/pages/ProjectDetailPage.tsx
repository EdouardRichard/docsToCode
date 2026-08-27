import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Card,
  Descriptions,
  Table,
  Tag,
  Button,
  Upload,
  Space,
  message,
  Popconfirm,
  Spin,
  Typography,
} from 'antd';
import { InboxOutlined, ReloadOutlined, DeleteOutlined, ArrowLeftOutlined, ClearOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { KnowledgeSource } from '../types';
import { getProject } from '../api/projects';
import { listKnowledgeSources, uploadKnowledgeSource, reprocessSource, deleteSource, clearScope } from '../api/knowledgeSources';
import { useSSE } from '../hooks/useSSE';
import type { Project } from '../types';

const { Dragger } = Upload;
const { Text } = Typography;

const STATUS_COLORS: Record<string, string> = {
  published: 'green',
  processing: 'blue',
  uploaded: 'orange',
  failed: 'red',
  deleted: 'default',
};

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);

  const fetchProject = useCallback(async () => {
    if (!id) return;
    try {
      const data = await getProject(id);
      setProject(data);
    } catch (err) {
      message.error(`Failed to load project: ${(err as Error).message}`);
    }
  }, [id]);

  const fetchSources = useCallback(async () => {
    if (!project?.knowledge_scope_id) return;
    try {
      const data = await listKnowledgeSources(project.knowledge_scope_id);
      setSources(data);
    } catch (err) {
      message.error(`Failed to load sources: ${(err as Error).message}`);
    }
  }, [project?.knowledge_scope_id]);

  useEffect(() => {
    setLoading(true);
    fetchProject().finally(() => setLoading(false));
  }, [fetchProject]);

  useEffect(() => {
    if (project) fetchSources();
  }, [project, fetchSources]);

  // SSE for real-time status updates
  const topics = project ? [`scope:${project.knowledge_scope_id}`] : [];
  const { lastEvent, connected } = useSSE(topics);

  // React to SSE events by refreshing sources
  useEffect(() => {
    if (lastEvent) {
      fetchSources();
      if (lastEvent.event === 'error') {
        message.error(lastEvent.data.message || 'Processing error occurred');
      }
    }
  }, [lastEvent, fetchSources]);

  const handleUpload = async (file: File) => {
    if (!project) return false;
    setUploading(true);
    try {
      await uploadKnowledgeSource(project.knowledge_scope_id, file);
      message.success(`${file.name} uploaded successfully`);
      fetchSources();
    } catch (err) {
      message.error(`Upload failed: ${(err as Error).message}`);
    } finally {
      setUploading(false);
    }
    return false; // prevent default upload behavior
  };

  const handleReprocess = async (sourceId: string) => {
    try {
      await reprocessSource(sourceId);
      message.success('Reprocessing started');
      fetchSources();
    } catch (err) {
      message.error(`Reprocess failed: ${(err as Error).message}`);
    }
  };

  const handleDeleteSource = async (sourceId: string) => {
    try {
      await deleteSource(sourceId);
      message.success('Source deleted');
      fetchSources();
    } catch (err) {
      message.error(`Delete failed: ${(err as Error).message}`);
    }
  };

  const handleClearScope = async () => {
    if (!project) return;
    try {
      await clearScope(project.knowledge_scope_id);
      message.success('Knowledge scope cleared');
      fetchSources();
    } catch (err) {
      message.error(`Clear scope failed: ${(err as Error).message}`);
    }
  };

  const columns: ColumnsType<KnowledgeSource> = [
    {
      title: 'Filename',
      dataIndex: 'filename',
      key: 'filename',
    },
    {
      title: 'Format',
      dataIndex: 'format',
      key: 'format',
      width: 100,
    },
    {
      title: 'Size',
      dataIndex: 'size_bytes',
      key: 'size_bytes',
      width: 120,
      render: (bytes: number) => `${(bytes / 1024).toFixed(1)} KB`,
    },
    {
      title: 'Status',
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (status: string) => (
        <Tag color={STATUS_COLORS[status] || 'default'}>{status.toUpperCase()}</Tag>
      ),
    },
    {
      title: 'Error',
      dataIndex: 'processing_error',
      key: 'processing_error',
      ellipsis: true,
      render: (err: string | undefined) =>
        err ? <Text type="danger">{err}</Text> : '-',
    },
    {
      title: 'Updated',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 180,
      render: (val: string) => new Date(val).toLocaleString(),
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 160,
      render: (_: unknown, record: KnowledgeSource) => (
        <Space>
          <Button
            size="small"
            icon={<ReloadOutlined />}
            disabled={record.status === 'processing'}
            onClick={() => handleReprocess(record.source_id)}
          >
            Reprocess
          </Button>
          <Popconfirm
            title="Delete this source?"
            description="This will delete the source and its indexed data."
            onConfirm={() => handleDeleteSource(record.source_id)}
            okText="Yes"
            cancelText="No"
          >
            <Button size="small" danger icon={<DeleteOutlined />}>
              Delete
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 100 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!project) {
    return <div>Project not found</div>;
  }

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/')}>
        Back to Projects
      </Button>

      <Card title="Project Details">
        <Descriptions column={2} bordered size="small">
          <Descriptions.Item label="Project ID">{project.project_id}</Descriptions.Item>
          <Descriptions.Item label="Name">{project.name}</Descriptions.Item>
          <Descriptions.Item label="Alias">{project.alias || '-'}</Descriptions.Item>
          <Descriptions.Item label="Repo Path">{project.repo_path || '-'}</Descriptions.Item>
          <Descriptions.Item label="Knowledge Scope ID">
            {project.knowledge_scope_id}
          </Descriptions.Item>
          <Descriptions.Item label="Created">
            {new Date(project.created_at).toLocaleString()}
          </Descriptions.Item>
          <Descriptions.Item label="Updated">
            {new Date(project.updated_at).toLocaleString()}
          </Descriptions.Item>
          <Descriptions.Item label="SSE Connection">
            <Tag color={connected ? 'green' : 'red'}>
              {connected ? 'Connected' : 'Disconnected'}
            </Tag>
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card
        title="Knowledge Sources"
        extra={
          <Popconfirm
            title="Clear all knowledge in this project?"
            description="This deletes all sources and their indexed data."
            onConfirm={handleClearScope}
            okText="Yes"
            cancelText="No"
          >
            <Button size="small" danger icon={<ClearOutlined />}>
              Clear Scope
            </Button>
          </Popconfirm>
        }
      >
        <Table
          rowKey="source_id"
          columns={columns}
          dataSource={sources}
          pagination={{ pageSize: 10 }}
          size="small"
        />
      </Card>

      <Card title="Upload Knowledge Source">
        <Dragger
          accept=".md,.java"
          multiple={false}
          showUploadList={false}
          disabled={uploading}
          beforeUpload={(file) => {
            handleUpload(file);
            return false;
          }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">Click or drag file to upload</p>
          <p className="ant-upload-hint">
            Supports .md (Markdown) and .java files only.
          </p>
        </Dragger>
      </Card>
    </Space>
  );
}
