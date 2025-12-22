import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { useMenu } from '@/store/menu';

export const useNavigationBreadcrumbs = (source: 'space' | 'manage' = 'manage') => {
  const location = useLocation();
  const { allMenus, updateBreadcrumbs } = useMenu();

  useEffect(() => {
    const currentPath = location.pathname;
    const menus = allMenus[source] || [];

    // 查找匹配的菜单项并构建keyPath
    const findMenuKeyPath = (menuList: any[]): string[] | null => {
      const checkDynamicMatch = (pattern: string, path: string) => {
        const pathPattern = pattern.replace(/:[\w-]+/g, '[^/]+');
        const regex = new RegExp(`^${pathPattern}$`);
        return regex.test(path);
      };
      
      for (const menu of menuList) {
        if (menu.subs && menu.subs.length > 0) {
          for (const sub of menu.subs) {
            // 检查三级菜单
            if (sub.subs && sub.subs.length > 0) {
              for (const subSub of sub.subs) {
                if (subSub.path) {
                  const subSubPath = subSub.path[0] !== '/' ? '/' + subSub.path : subSub.path;
                  if (subSubPath === currentPath || (subSubPath.includes(':') && checkDynamicMatch(subSubPath, currentPath))) {
                    return [subSub.path, `${sub.id}`, `${menu.id}`];
                  }
                }
              }
            }
            
            // 检查二级菜单
            if (sub.path) {
              const subPath = sub.path[0] !== '/' ? '/' + sub.path : sub.path;
              if (subPath === currentPath || (subPath.includes(':') && checkDynamicMatch(subPath, currentPath))) {
                return [sub.path, `${menu.id}`];
              }
            }
          }
        }
        
        // 检查一级菜单
        if (menu.path) {
          const menuPath = menu.path[0] !== '/' ? '/' + menu.path : menu.path;
          if (menuPath === currentPath || (menuPath.includes(':') && checkDynamicMatch(menuPath, currentPath))) {
            return [menu.path];
          }
        }
      }
      
      return null;
    };

    const keyPath = findMenuKeyPath(menus);
    if (keyPath) {
      updateBreadcrumbs(keyPath, source);
    }
  }, [location.pathname, allMenus, source, updateBreadcrumbs]);
};