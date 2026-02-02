/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:11:43 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:11:43 
 */
/**
 * AuthSpaceLayout Component
 * 
 * The authenticated layout wrapper for knowledge base (space) pages that provides:
 * - Route authentication and permission checks for space context
 * - Automatic breadcrumb navigation updates
 * - Sidebar navigation and header configured for space mode
 * - Token-based authentication validation
 * - Storage type initialization
 * 
 * @component
 */

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

/**
 * Authentication layout component for knowledge base (space) pages.
 * Similar to AuthLayout but configured for space context with storage type management.
 */
const AuthSpaceLayout: FC = () => {
  const { getUserInfo, getStorageType } = useUser();
  
  // Use route guard hook to handle authentication and permission checks for space context
  useRouteGuard('space');
  
  // Automatically update breadcrumb navigation based on current route in space context
  useNavigationBreadcrumbs('space');
  
  // Check authentication token, fetch user info and storage type on mount
  useEffect(() => {
    const authToken = cookieUtils.get('authToken')
    if (!authToken && !window.location.hash.includes('#/login')) {
      window.location.href = `/#/login`;
    } else {
      getUserInfo()
      getStorageType() // Fetch storage type for knowledge base operations
    }
  }, []);

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {/* Sidebar navigation configured for space mode */}
      <Sider source="space" />
      <Layout style={{maxHeight: '100vh', width: '100vh', overflowY: 'auto' }}>
        {/* Header with breadcrumbs and user menu configured for space mode */}
        <AppHeader source="space" />
        {/* Main content area for knowledge base pages - renders child routes */}
        <Content style={{ padding: '16px 17px 24px 16px', zIndex: 0, height: 'calc(100vh - 64px)', overflowY: 'auto' }}>
          <Outlet />
        </Content> 
      </Layout>
    </Layout>
  )
};

export default AuthSpaceLayout;