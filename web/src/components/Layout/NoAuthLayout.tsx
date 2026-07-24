/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:13:55 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-14 16:14:36
 */
/**
 * NoAuthLayout Component
 * 
 * A minimal layout wrapper for public pages that don't require authentication.
 * Provides a simple container without navigation UI or authentication checks.
 * 
 * @component
 */

import { Outlet } from 'react-router-dom';
import { type FC } from 'react';

import ErrorBoundary from '@/components/ErrorBoundary'
/**
 * No-authentication layout component for public pages.
 * Renders child routes in a simple full-size container without any auth requirements.
 */
const NoAuthLayout: FC = () => {

  return (
    <div className="rb:relative rb:h-full rb:w-full">
      <ErrorBoundary>
        {/* Render public pages without authentication */}
        <Outlet />
      </ErrorBoundary>
    </div>
  )
};

export default NoAuthLayout;