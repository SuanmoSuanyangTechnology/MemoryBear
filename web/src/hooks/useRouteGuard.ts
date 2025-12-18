import { useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useMenu, type MenuItem } from '@/store/menu'

// 模拟认证状态检查函数
export const checkAuthStatus = (): boolean => {
  // 在实际应用中，这里应该检查localStorage或cookie中的认证信息
  // 这里为了演示，我们假设首页不需要认证，其他页面需要认证
  return true; // 暂时返回true以便测试
};

// 递归检查路由是否存在于菜单数据中
export const checkRoutePermission = (menus: MenuItem[], currentPath: string): boolean => {
  // 首页和知识库相关页面默认有权限
  if (currentPath === '/' || currentPath.includes('knowledge-detail') || currentPath.includes('knowledge-base')) {
    return true;
  }
  
  for (const menu of menus) {
    // 检查当前菜单的path是否匹配
    if (menu.path && currentPath.includes(menu.path)) {
      return true;
    }
    // 递归检查子菜单
    if (menu.subs && menu.subs.length > 0) {
      if (checkRoutePermission(menu.subs, currentPath)) {
        return true;
      }
    }
  }
  
  return false;
};

// 路由守卫Hook，用于处理路由权限检查
export const useRouteGuard = (source: 'space' | 'manage') => {
  const navigate = useNavigate();
  const location = useLocation();
  const { allMenus } = useMenu();
  const menus = allMenus[source];
  
  // 确保在路由变化时重新执行所有检查逻辑
  useEffect(() => {
    // 模拟认证检查逻辑
    const isAuthenticated = checkAuthStatus();
    
    if (!isAuthenticated && location.pathname !== '/') {
      // TODO: 未认证用户重定向到登录页（这里是首页）
      navigate('/', { replace: true });
      return;
    }
    
    // 认证通过后，检查路由权限
    if (isAuthenticated && location.pathname !== '/' && location.pathname !== '/not-found') {
      const hasPermission = checkRoutePermission(menus, location.pathname);
      if (!hasPermission) {
        // 无权限访问该路由，重定向到无权限页面
        // navigate('/no-permission', { replace: true });
      }
    }
  }, [navigate, location.pathname, location.search, location.hash, menus]);
  
  // 返回当前路径和权限状态，确保组件能感知到路由变化
  return {
    currentPath: location.pathname,
    search: location.search,
    hash: location.hash,
    isChecking: false, // 可以扩展添加加载状态
  };
};

export default useRouteGuard;