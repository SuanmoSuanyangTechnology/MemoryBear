/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:12:42 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-08-14 17:45:32
 */
/**
 * BasicAuthLayout Component
 * 
 * A minimal layout wrapper that provides:
 * - User information initialization
 * - Storage type initialization
 * - Simple container for child routes without navigation UI
 * 
 * Used for pages that don't require sidebar/header (e.g., login, public pages).
 * 
 * @component
 */

import { Outlet } from 'react-router-dom';
import { useEffect, type FC } from 'react';
import { Layout, Flex } from 'antd';

import { useUser } from '@/store/user';
import ErrorBoundary from '@/components/ErrorBoundary'
import Banners from './Banner';

/**
 * Basic layout component for pages without navigation UI.
 * Fetches user info and storage type on mount, then renders child routes.
 */
const BasicAuthLayout: FC = () => {
  const { getUserInfo } = useUser();
  
  // Fetch user information and storage type on component mount
  useEffect(() => {
    getUserInfo(undefined, true); // Pass true to skip navigation jump
  }, [getUserInfo]);

  return (
    <Layout className="rb:min-h-screen!">
      <Flex vertical gap={0} className="rb:h-screen! rb:w-screen! rb:overflow-hidden! rb:pb-3!">
        <Banners className="rb:mb-0!" />
        <div className="rb:flex-1 rb:h-0! rb:min-h-0! rb:overflow-hidden!">
          <ErrorBoundary>
            {/* Render child routes without additional UI */}
            <Outlet />
          </ErrorBoundary>
        </div>
      </Flex>
    </Layout>
  )
};

export default BasicAuthLayout;