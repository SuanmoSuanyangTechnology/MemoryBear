/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:11:02 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-07-14 16:13:15 
 */
/**
 * AuthLayout Component
 * 
 * The main authenticated layout wrapper that provides:
 * - Route authentication and permission checks
 * - Automatic breadcrumb navigation updates
 * - Sidebar navigation and header
 * - Token-based authentication validation
 * 
 * @component
 */

import { Outlet } from 'react-router-dom';
import { useEffect, type FC } from 'react';
import { Layout } from 'antd';

import useRouteGuard from '@/hooks/useRouteGuard';
import { useNavigationBreadcrumbs } from '@/hooks/useNavigationBreadcrumbs';
import AppHeader from '@/components/Header';
import Sider from '@/components/SiderMenu'
import ErrorBoundary from '@/components/ErrorBoundary'
import { useUser } from '@/store/user';
import { cookieUtils } from '@/utils/request';
import Banners from './Banner';


const { Content } = Layout;

/**
 * Authentication layout component that wraps all authenticated pages.
 * Handles route guards, breadcrumb navigation, and user authentication.
 */
const AuthLayout: FC = () => {
  const { getUserInfo } = useUser();
  
  // Use route guard hook to handle authentication and permission checks
  useRouteGuard('manage');
  
  // Automatically update breadcrumb navigation based on current route
  useNavigationBreadcrumbs('manage');
  
  // Check authentication token and fetch user info on mount
  useEffect(() => {
    const authToken = cookieUtils.get('authToken')
    if (!authToken && !window.location.hash.includes('#/login')) {
      window.location.href = `/#/login`;
    } else {
      getUserInfo()
    }
  }, []);

  return (
    <Layout className="rb:min-h-screen!">
      {/* Sidebar navigation */}
      <Sider />
      <Layout className="rb:max-h-screen! rb:w-screen! rb:overflow-y-auto!">
        {/* Header with breadcrumbs and user menu */}
        <AppHeader />
        {/* Main content area - renders child routes */}
        <Content className="rb:px-3! rb:pb-3! rb:z-0! rb:flex-1!">
          <Banners />
          {/* Keep sidebar/header visible when a lazy route chunk fails to load */}
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
        </Content>
      </Layout>
    </Layout>
  )
};

export default AuthLayout;