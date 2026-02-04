/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:13:38 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:13:38 
 */
/**
 * LoginLayout Component
 * 
 * A minimal layout wrapper for authentication pages (login, register, etc.).
 * Provides a simple container without navigation UI or authentication checks.
 * 
 * @component
 */

import { Outlet } from 'react-router-dom';
import { type FC } from 'react';

/**
 * Login layout component for unauthenticated pages.
 * Renders child routes in a simple full-size container.
 */
const LoginLayout: FC = () => {

  return (
    <div className="rb:relative rb:h-full rb:w-full">
      {/* Render authentication pages (login, register, etc.) */}
      <Outlet />
    </div>
  )
};

export default LoginLayout;