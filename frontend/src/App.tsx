import { Routes, Route } from 'react-router-dom';
import { ConfigProvider, Layout } from 'antd';
import ProjectsPage from './pages/ProjectsPage';
import ProjectDetailPage from './pages/ProjectDetailPage';

const { Header, Content } = Layout;

export default function App() {
  return (
    <ConfigProvider>
      <Layout style={{ minHeight: '100vh' }}>
        <Header style={{ color: '#fff', fontSize: 20, fontWeight: 600 }}>
          RAG MCP Management
        </Header>
        <Content style={{ padding: 24 }}>
          <Routes>
            <Route path="/" element={<ProjectsPage />} />
            <Route path="/projects/:id" element={<ProjectDetailPage />} />
          </Routes>
        </Content>
      </Layout>
    </ConfigProvider>
  );
}
