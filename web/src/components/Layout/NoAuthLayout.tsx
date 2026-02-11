/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:13:55 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-02 15:52:17
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

/**
 * No-authentication layout component for public pages.
 * Renders child routes in a simple full-size container without any auth requirements.
 */
const NoAuthLayout: FC = () => {

  return (
    <div className="rb:relative rb:h-full rb:w-full">
      {/* Render public pages without authentication */}
      <Outlet />
    </div>
  )
};

export default NoAuthLayout;