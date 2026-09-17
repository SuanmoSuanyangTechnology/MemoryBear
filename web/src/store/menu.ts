/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:33:34 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-04 10:31:14
 */
/**
 * Menu Store
 * 
 * Manages application menu and breadcrumb navigation with:
 * - Menu loading from JSON configuration
 * - Sidebar collapse state
 * - Breadcrumb generation from menu paths
 * - Custom breadcrumb support
 * - Separate menu contexts (space/manage)
 * 
 * @store
 */

import { create } from 'zustand'
import AllMenus from './menuData'

/** Menu item interface */
export interface MenuItem {
  id: number;
  parent: number;
  code: string | null;
  label: string;
  i18nKey: string | null;
  path: string | null;
  enable: boolean;
  display: boolean;
  level: number;
  sort: number;
  icon?: string | null;
  active_icon?: string | null;
  menuDesc?: string | null;
  deleted?: string | null;
  updateTime?: number;
  new_?: string | null;
  keepAlive?: boolean;
  master?: string | null;
  disposable?: boolean;
  appSystem?: string | null;
  type?: 'group' | string;
  subs?: MenuItem[] | null;
  onClick?: (e?: React.MouseEvent) => void | boolean;
}

/** Menu state interface */
interface MenuState {
  /** Sidebar collapsed state */
  collapsed: boolean;
  /** Toggle sidebar collapse */
  toggleSider: () => void;
  /** All menus by context */
  allMenus: Record<'space' | 'manage', MenuItem[]>;
  /** All breadcrumbs by context */
  allBreadcrumbs: Record<'space' | 'manage' | string, MenuItem[]>;
  /** Load menus for specific context */
  loadMenus: (source: 'space' | 'manage') => void;
  /** Update breadcrumbs based on key path */
  updateBreadcrumbs: (keyPath: string[], source: 'space' | 'manage') => void;
  /** Set custom breadcrumbs */
  setCustomBreadcrumbs: (breadcrumbs: MenuItem[], source: string) => void;
}

/** Initialize breadcrumbs from localStorage */
const initBreadcrumbs = localStorage.getItem('breadcrumbs') || '[]'

/** Menu store */
export const useMenu = create<MenuState>((set, get) => ({
  collapsed: localStorage.getItem('collapsed') === 'true',
  allMenus: {
    manage: [],
    space: []
  },
  allBreadcrumbs: JSON.parse(initBreadcrumbs),
  loadMenus: async () => {
    set({ allMenus: AllMenus })
  },
  toggleSider: () => {
    set((state) => {
      const newCollapsed = !state.collapsed
      localStorage.setItem('collapsed', JSON.stringify(newCollapsed))
      return { collapsed: newCollapsed }
    })
  },
  updateBreadcrumbs: (paths, source) => {
    const { allMenus } = get()
    const menus = allMenus[source] || []
    const result: MenuItem[] = []

    const pathMatches = (pattern: string, path: string) => {
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

    /** Rebuild the breadcrumb from a route pattern and an arbitrary-depth ancestor id chain. */
    const ancestorIds = paths.slice(1).reverse();
    let currentLevel = menus;

    for (const id of ancestorIds) {
      const matchedAncestor = currentLevel.find(menu => `${menu.id}` === id);
      if (!matchedAncestor) break;

      result.push({ ...matchedAncestor, subs: null });
      currentLevel = matchedAncestor.subs || [];
    }

    const matchedRoute = currentLevel.find(menu => (
      menu.path === paths[0] || pathMatches(menu.path || '', paths[0])
    ));
    if (matchedRoute) {
      result.push({ ...matchedRoute, subs: null });
    }

    const allBreadcrumbs = { ...get().allBreadcrumbs, [source]: result }
    set({ allBreadcrumbs })
    localStorage.setItem('breadcrumbs', JSON.stringify(allBreadcrumbs))
  },
  setCustomBreadcrumbs: (breadcrumbs, source) => {
    const allBreadcrumbs = { ...get().allBreadcrumbs, [source]: breadcrumbs }
    set({ allBreadcrumbs })
    localStorage.setItem('breadcrumbs', JSON.stringify(allBreadcrumbs))
  },
}))
