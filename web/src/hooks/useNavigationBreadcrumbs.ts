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
      if (normalized.includes(':')) {
        const regex = new RegExp('^' + normalized.replace(/:[\\w-]+/g, '[^/]+') + '$');
        return regex.test(path);
      }
      return false;
    };

    /**
     * Recursively search menu tree, returns keyPath or null.
     * keyPath format:
     *  - 1-level: [path]
     *  - 2-level: [subPath, parentId]
     *  - 3-level: [subSubPath, subId, parentId]
     */
    /**
     * parentId: the group's id when recursing into group subs
     * keyPath format:
     *  - 1-level: [path]
     *  - 2-level: [subPath, parentId]
     *  - 3-level: [subSubPath, subId, parentId]
     */
    const findKeyPath = (menuList: any[], groupId?: string): string[] | null => {
      for (const menu of menuList) {
        /** Group menus: recurse into subs, passing group id */
        if (menu.type === 'group' && menu.subs?.length) {
          const result = findKeyPath(menu.subs, `${menu.id}`);
          if (result) return result;
          continue;
        }

        if (menu.subs?.length) {
          for (const sub of menu.subs) {
            /** Check third-level subs */
            if (sub.subs?.length) {
              for (const subSub of sub.subs) {
                if (subSub.path && pathMatches(subSub.path, currentPath)) {
                  return [subSub.path, `${sub.id}`, `${menu.id}`];
                }
              }
            }
            /** Second-level match: sub is a leaf under menu */
            if (sub.path && pathMatches(sub.path, currentPath)) {
              /** If menu is inside a group, return 3-level: [subPath, menuId, groupId] */
              return groupId
                ? [sub.path, `${menu.id}`, groupId]
                : [sub.path, `${menu.id}`];
            }
          }
        }

        /** First-level / group-child match */
        if (menu.path && pathMatches(menu.path, currentPath)) {
          return groupId ? [menu.path, groupId] : [menu.path];
        }
      }
      return null;
    };

    const keyPath = findKeyPath(menus);

    console.log('keyPath', keyPath)
    if (keyPath) {
      updateBreadcrumbs(keyPath, source);
    }
  }, [location.pathname, allMenus, source, updateBreadcrumbs]);
};
