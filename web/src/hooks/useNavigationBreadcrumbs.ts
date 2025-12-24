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
    const findMenuKeyPath = (menuList: any[], parentKeys: string[] = []): string[] | null => {
      let bestMatch: { path: string; parentId?: string; score: number } | null = null;
      
      for (const menu of menuList) {
        // 检查子菜单
        if (menu.subs && menu.subs.length > 0) {
          const menuPath = menu.path ? (menu.path[0] !== '/' ? '/' + menu.path : menu.path) : '';
          for (const sub of menu.subs) {
            if (sub.path) {
              const subPath = sub.path[0] !== '/' ? '/' + sub.path : sub.path;
              
              // 精确匹配优先
              if (subPath === currentPath) {
                return [sub.path, `${menu.id}`];
              }
              console.log('menuPath', menuPath)
              // 动态路由匹配
              if (subPath.includes(':')) {
                // 检查是否在父菜单下
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
                // 直接匹配子菜单路径
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
        
        // 检查主菜单
        if (menu.path) {
          const menuPath = menu.path[0] !== '/' ? '/' + menu.path : menu.path;
          // 精确匹配优先
          if (menuPath === currentPath) {
            return [menu.path, ...parentKeys].reverse();
          }
          // 动态路由匹配
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