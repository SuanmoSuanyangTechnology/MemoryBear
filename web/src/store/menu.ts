/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:33:34 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 16:33:34 
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
import AllMenus from './menu.json'

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
  iconActive?: string | null;
  menuDesc?: string | null;
  deleted?: string | null;
  updateTime?: number;
  new_?: string | null;
  keepAlive?: boolean;
  master?: string | null;
  disposable?: boolean;
  appSystem?: string | null;
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
    let result: MenuItem[] = []
    
    console.log('updateBreadcrumbs paths:', paths);
    
    if (paths.length === 3) {
      /** Three-level menu: [subSubPath, subId, menuId] */
      const menuId = paths[2];
      const subId = paths[1];
      const subSubPath = paths[0];
      
      const matchedMenu = menus.find(menu => `${menu.id}` === menuId);
      if (matchedMenu && matchedMenu.subs) {
        const matchedSub = matchedMenu.subs.find(sub => `${sub.id}` === subId);
        if (matchedSub && matchedSub.subs) {
          const matchedSubSub = matchedSub.subs.find(subSub => subSub.path === subSubPath);
          if (matchedSubSub) {
            result = [
              { ...matchedMenu, subs: null },
              { ...matchedSub, subs: null },
              { ...matchedSubSub, subs: null }
            ];
          }
        }
      }
    } else {
      /** Original logic for one-level and two-level menus */
      const matchedMenu: MenuItem | undefined = menus.find(menu => menu.path === paths[paths.length - 1] || `${menu.id}` === paths[1]);

      if (matchedMenu) {
        let matchedSubMenu: MenuItem | undefined = undefined;
        if (paths.length > 1 && matchedMenu?.subs?.length) {
          matchedSubMenu = matchedMenu.subs.find(menu => menu.path === paths[0]);
        }
        result = [
          { ...matchedMenu, subs: null },
          matchedSubMenu
        ].filter(item => item !== undefined) as MenuItem[]
      } else {
        result = [] as MenuItem[]
      }
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
