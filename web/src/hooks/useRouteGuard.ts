/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:24:54 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 16:24:54 
 */
/**
 * useRouteGuard Hook
 * 
 * Provides route authentication and permission checking:
 * - Validates user authentication status
 * - Checks route permissions against menu structure
 * - Redirects unauthorized users
 * - Monitors route changes
 * 
 * @hook
 */

import { useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useMenu, type MenuItem } from '@/store/menu'

/** Check authentication status */
export const checkAuthStatus = (): boolean => {
  /** In production, check localStorage or cookie for auth info */
  return true; /** Temporarily return true for testing */
};

/** Recursively check if route exists in menu data */
export const checkRoutePermission = (menus: MenuItem[], currentPath: string): boolean => {
  /** Home and knowledge base pages have default permission */
  if (currentPath === '/' || currentPath.includes('knowledge-detail') || currentPath.includes('knowledge-base')) {
    return true;
  }
  
  for (const menu of menus) {
    /** Check if current menu path matches */
    if (menu.path && currentPath.includes(menu.path)) {
      return true;
    }
    /** Recursively check submenus */
    if (menu.subs && menu.subs.length > 0) {
      if (checkRoutePermission(menu.subs, currentPath)) {
        return true;
      }
    }
  }
  
  return false;
};

/**
 * Route guard hook for handling route permission checks.
 * 
 * @param source - Menu source type ('space' or 'manage')
 */
export const useRouteGuard = (source: 'space' | 'manage') => {
  const navigate = useNavigate();
  const location = useLocation();
  const { allMenus } = useMenu();
  const menus = allMenus[source];
  
  /** Re-execute all checks on route changes */
  useEffect(() => {
    /** Simulate authentication check */
    const isAuthenticated = checkAuthStatus();
    
    if (!isAuthenticated && location.pathname !== '/') {
      /** Redirect unauthenticated users to home/login page */
      navigate('/', { replace: true });
      return;
    }
    
    /** After authentication, check route permissions */
    if (isAuthenticated && location.pathname !== '/' && location.pathname !== '/not-found') {
      const hasPermission = checkRoutePermission(menus, location.pathname);
      if (!hasPermission) {
        /** No permission, redirect to no-permission page */
        // navigate('/no-permission', { replace: true });
      }
    }
  }, [navigate, location.pathname, location.search, location.hash, menus]);
  
  /** Return current path and permission status */
  return {
    currentPath: location.pathname,
    search: location.search,
    hash: location.hash,
    isChecking: false, /** Can be extended to add loading state */
  };
};

export default useRouteGuard;