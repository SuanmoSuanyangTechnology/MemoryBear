/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:12:42 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-26 15:36:25
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

import { useUser } from '@/store/user';

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
    <div className="rb:relative rb:min-h-screen rb:w-screen">
      {/* Render child routes without additional UI */}
      <Outlet />
    </div>
  )
};

export default BasicAuthLayout;