/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:24:49 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 16:24:49 
 */
/**
 * useNavigationBreadcrumbs Hook
 * 
 * Automatically updates breadcrumbs based on current route:
 * - Matches current path against menu structure
 * - Supports dynamic routes with parameters
 * - Handles nested menu hierarchies
 * - Updates breadcrumbs on route changes
 * 
 * @hook
 */

import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { useMenu } from '@/store/menu';

/**
 * Hook to automatically update breadcrumbs based on navigation.
 * 
 * @param source - Menu source type ('space' or 'manage')
 */
export const useNavigationBreadcrumbs = (source: 'space' | 'manage' = 'manage') => {
  const location = useLocation();
  const { allMenus, updateBreadcrumbs } = useMenu();

  useEffect(() => {
    const currentPath = location.pathname;
    const menus = allMenus[source] || [];

    /** Find matching menu item and build key path */
    const findMenuKeyPath = (menuList: any[], parentKeys: string[] = []): string[] | null => {
      let bestMatch: { path: string; parentId?: string; score: number } | null = null;
      
      for (const menu of menuList) {
        /** Check submenus */
        if (menu.subs && menu.subs.length > 0) {
          const menuPath = menu.path ? (menu.path[0] !== '/' ? '/' + menu.path : menu.path) : '';
          for (const sub of menu.subs) {
            if (sub.path) {
              const subPath = sub.path[0] !== '/' ? '/' + sub.path : sub.path;
              
              /** Exact match has priority */
              if (subPath === currentPath) {
                return [sub.path, `${menu.id}`];
              }
              console.log('menuPath', menuPath)
              /** Dynamic route matching */
              if (subPath.includes(':')) {
                /** Check if under parent menu */
                if (menuPath && currentPath.startsWith(menuPath + '/')) {
                  const relativePath = currentPath.replace(menuPath, '');
                  const pathSegments = subPath.split('/');
                  const relativeSegments = relativePath.split('/');
                  if (pathSegments.length === relativeSegments.length) {
                    const pathPattern = subPath.replace(/:[\w-]+/g, '[^/]+').replace(/\[[\w-]+\]/g, '[^/]+');
                    const regex = new RegExp(`^${pathPattern}$`);
                    if (regex.test(relativePath)) {
                      return [sub.path, `${menu.id}`];
                    }
                  }
                }
                /** Direct match submenu path */
                const pathSegments = subPath.split('/');
                const currentSegments = currentPath.split('/');
                if (pathSegments.length === currentSegments.length) {
                  const pathPattern = subPath.replace(/:[\w-]+/g, '[^/]+').replace(/\[[\w-]+\]/g, '[^/]+');
                  const regex = new RegExp(`^${pathPattern}$`);
                  if (regex.test(currentPath)) {
                    return [sub.path, `${menu.id}`];
                  }
                }
              }
            }
          }
        }
        
        /** Check main menu */
        if (menu.path) {
          const menuPath = menu.path[0] !== '/' ? '/' + menu.path : menu.path;
          /** Exact match has priority */
          if (menuPath === currentPath) {
            return [menu.path, ...parentKeys].reverse();
          }
          /** Dynamic route matching */
          if (menuPath.includes(':')) {
            const pathSegments = menuPath.split('/');
            const currentSegments = currentPath.split('/');
            if (pathSegments.length === currentSegments.length) {
              const pathPattern = menuPath.replace(/:[\w-]+/g, '[^/]+').replace(/\[[\w-]+\]/g, '[^/]+');
              const regex = new RegExp(`^${pathPattern}$`);
              if (regex.test(currentPath)) {
                const score = menuPath.split('/').length;
                if (!bestMatch || score > bestMatch.score) {
                  bestMatch = { path: menu.path, score };
                }
              }
            }
          } else if (currentPath.startsWith(menuPath + '/')) {
            const score = menuPath.split('/').length;
            if (!bestMatch || score > bestMatch.score) {
              bestMatch = { path: menu.path, score };
            }
          }
        }
      }
      
      if (bestMatch) {
        return bestMatch.parentId ? [bestMatch.path, bestMatch.parentId] : [bestMatch.path];
      }
      return null;
    };

    const keyPath = findMenuKeyPath(menus);
    if (keyPath) {
      updateBreadcrumbs(keyPath, source);
    }
  }, [location.pathname, allMenus, source, updateBreadcrumbs]);
};