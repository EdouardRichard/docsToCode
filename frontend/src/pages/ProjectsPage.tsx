import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Table, Button, Modal, Form, Input, Space, message, Card } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { Project } from '../types';
import { listProjects, createProject, deleteProject } from '../api/projects';
import type { CreateProjectInput } from '../api/projects';

export default function ProjectsPage() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm<CreateProjectInput>();

  const fetchProjects = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listProjects();
      setProjects(data);
    } catch (err) {
      message.error(`Failed to load projects: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  const handleCreate = async () => {
    try {
      const values = await form.validateFields();
      setCreating(true);
      await createProject(values);
      message.success('Project created');
      setModalOpen(false);
      form.resetFields();
      fetchProjects();
    } catch (err) {
      if ((err as { errorFields?: unknown }).errorFields) return; // validation error
      message.error(`Failed to create project: ${(err as Error).message}`);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteProject(id);
      message.success('Project deleted');
      fetchProjects();
    } catch (err) {
      message.error(`Failed to delete project: ${(err as Error).message}`);
    }
  };

  const columns: ColumnsType<Project> = [
    {
      title: 'Name',
      dataIndex: 'name',
      key: 'name',
      render: (text: string, record: Project) => (
        <a onClick={() => navigate(`/projects/${record.project_id}`)}>{text}</a>
      ),
    },
    {
      title: 'Alias',
      dataIndex: 'alias',
      key: 'alias',
    },
    {
      title: 'Repo Path',
      dataIndex: 'repo_path',
      key: 'repo_path',
      ellipsis: true,
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (val: string) => new Date(val).toLocaleString(),
    },
    {
      title: 'Actions',
      key: 'actions',
      render: (_: unknown, record: Project) => (
        <Space>
          <Button size="small" onClick={() => navigate(`/projects/${record.project_id}`)}>
            View
          </Button>
          <Button size="small" danger onClick={() => handleDelete(record.project_id)}>
            Delete
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Card title="Projects" extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>Create Project</Button>}>
      <Table
        rowKey="project_id"
        columns={columns}
        dataSource={projects}
        loading={loading}
        pagination={{ pageSize: 10 }}
      />

      <Modal
        title="Create Project"
        open={modalOpen}
        onOk={handleCreate}
        onCancel={() => { setModalOpen(false); form.resetFields(); }}
        confirmLoading={creating}
        okText="Create"
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="Name" rules={[{ required: true, message: 'Project name is required' }]}>
            <Input placeholder="Enter project name" />
          </Form.Item>
          <Form.Item name="alias" label="Alias">
            <Input placeholder="Optional alias" />
          </Form.Item>
          <Form.Item name="repo_path" label="Repository Path">
            <Input placeholder="/path/to/repo" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
