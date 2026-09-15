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

    const pathMatches = (pattern: string, path: string): boolean => {
      const normalized = pattern[0] !== '/' ? '/' + pattern : pattern;
      if (normalized === path) return true;

      const regexPattern = normalized
        .split('/')
        .map(segment => (
          segment.startsWith(':')
            ? '[^/]+'
            : segment.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
        ))
        .join('/');

      return new RegExp(`^${regexPattern}$`).test(path);
    };

    /**
     * Recursively search the complete menu tree.
     * The first item is the matched route pattern, followed by ancestor ids
     * from the nearest parent to the root.
     */
    const findKeyPath = (menuList: any[], ancestorIds: string[] = []): string[] | null => {
      for (const menu of menuList) {
        const nextAncestorIds = [...ancestorIds, `${menu.id}`];

        /** Prefer the deepest route when parent and child paths overlap. */
        if (menu.subs?.length) {
          const result = findKeyPath(menu.subs, nextAncestorIds);
          if (result) return result;
        }

        if (menu.path && pathMatches(menu.path, currentPath)) {
          return [menu.path, ...[...ancestorIds].reverse()];
        }
      }
      return null;
    };

    const keyPath = findKeyPath(menus);

    if (keyPath) {
      updateBreadcrumbs(keyPath, source);
    }
  }, [location.pathname, allMenus, source, updateBreadcrumbs]);
};
