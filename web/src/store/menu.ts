import { create } from 'zustand'
import AllMenus from './menu.json'

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
  subs: MenuItem[] | null;
}
interface MenuState {
  collapsed: boolean;
  toggleSider: () => void;
  allMenus: Record<'space' | 'manage', MenuItem[]>;
  allBreadcrumbs: Record<'space' | 'manage' | string, MenuItem[]>;
  loadMenus: (source: 'space' | 'manage') => void;
  updateBreadcrumbs: (keyPath: string[], source: 'space' | 'manage') => void;
  setCustomBreadcrumbs: (breadcrumbs: MenuItem[], source: 'space' | 'manage') => void;
}

const initBreadcrumbs = localStorage.getItem('breadcrumbs') || '[]'
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