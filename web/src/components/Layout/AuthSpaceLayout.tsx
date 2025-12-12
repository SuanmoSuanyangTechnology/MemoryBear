import { Outlet } from 'react-router-dom';
import { useEffect, type FC } from 'react';
import { Layout } from 'antd';
import useRouteGuard from '@/hooks/useRouteGuard';
import { useNavigationBreadcrumbs } from '@/hooks/useNavigationBreadcrumbs';
import AppHeader from '@/components/Header';
import Sider from '@/components/SiderMenu';
import { useUser } from '@/store/user';
import { cookieUtils } from '@/utils/request';


const { Content } = Layout;

// 认证布局组件，使用useRouteGuard hook进行路由鉴权
const AuthSpaceLayout: FC = () => {
  const { getUserInfo, getStorageType } = useUser();
  // 使用路由守卫hook处理认证和权限检查
  useRouteGuard('space');
  // 自动更新面包屑导航
  useNavigationBreadcrumbs('space');
  useEffect(() => {
    const authToken = cookieUtils.get('authToken')
    if (!authToken && !window.location.hash.includes('#/login')) {
      window.location.href = `/#/login`;
    } else {
      getUserInfo()
      getStorageType()
    }
  }, []);

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider source="space" />
      <Layout style={{maxHeight: '100vh', width: '100vh', overflowY: 'auto' }}>
        <AppHeader source="space" />
        <Content style={{ padding: '16px 17px 24px 16px', zIndex: 0, height: 'calc(100vh - 64px)', overflowY: 'auto' }}>
          <Outlet />
        </Content> 
      </Layout>
    </Layout>
  )
};

export default AuthSpaceLayout;