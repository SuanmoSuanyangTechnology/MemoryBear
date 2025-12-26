import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMenu } from '@/store/menu';
import type { MenuItem } from '@/store/menu';

export interface BreadcrumbItem {
  id: string;
  name: string;
  type?: 'knowledgeBase' | 'folder' | 'document';
}

export interface BreadcrumbPath {
  knowledgeBaseFolderPath: BreadcrumbItem[]; // 知识库文件夹路径
  knowledgeBase?: BreadcrumbItem; // 知识库信息
  documentFolderPath: BreadcrumbItem[]; // 文档文件夹路径
  document?: BreadcrumbItem; // 文档信息
}

export interface BreadcrumbOptions {
  onKnowledgeBaseMenuClick?: () => void;
  onKnowledgeBaseFolderClick?: (folderId: string, folderPath: BreadcrumbItem[]) => void;
  // 新增：区分面包屑类型
  breadcrumbType?: 'list' | 'detail';
}

export const useBreadcrumbManager = (options?: BreadcrumbOptions) => {
  const { allBreadcrumbs, setCustomBreadcrumbs } = useMenu();
  const navigate = useNavigate();

  const updateBreadcrumbs = useCallback((breadcrumbPath: BreadcrumbPath) => {
    const breadcrumbType = options?.breadcrumbType || 'list';
    
    // 对于详情页面，直接使用固定的知识库管理面包屑，不依赖可能被污染的 allBreadcrumbs
    let baseBreadcrumbs: MenuItem[] = [];
    
    if (breadcrumbType === 'detail') {
      // 详情页面：始终使用固定的知识库管理面包屑
      baseBreadcrumbs = [
        {
          id: 6,
          parent: 0,
          code: 'knowledge',
          label: '知识库',
          i18nKey: 'menu.knowledgeManagement',
          path: '/knowledge-base',
          enable: true,
          display: true,
          level: 1,
          sort: 0,
          icon: null,
          iconActive: null,
          menuDesc: null,
          deleted: null,
          updateTime: 0,
          new_: null,
          keepAlive: false,
          master: null,
          disposable: false,
          appSystem: null,
          subs: [],
        }
      ];
    } else {
      // 列表页面：从 space 获取基础面包屑，但确保包含知识库管理
      const spaceBreadcrumbs = allBreadcrumbs['space'] || [];
      const knowledgeBaseMenuIndex = spaceBreadcrumbs.findIndex(item => item.path === '/knowledge-base');
      
      if (knowledgeBaseMenuIndex >= 0) {
        baseBreadcrumbs = spaceBreadcrumbs.slice(0, knowledgeBaseMenuIndex + 1);
      } else {
        // 如果没有找到知识库菜单，使用默认的知识库管理面包屑
        baseBreadcrumbs = [
          {
            id: 6,
            parent: 0,
            code: 'knowledge',
            label: '知识库',
            i18nKey: 'menu.knowledgeManagement',
            path: '/knowledge-base',
            enable: true,
            display: true,
            level: 1,
            sort: 0,
            icon: null,
            iconActive: null,
            menuDesc: null,
            deleted: null,
            updateTime: 0,
            new_: null,
            keepAlive: false,
            master: null,
            disposable: false,
            appSystem: null,
            subs: [],
          }
        ];
      }
    }
    
    const filteredBaseBreadcrumbs = baseBreadcrumbs;

    // 给"知识库管理"添加点击事件
    const breadcrumbsWithClick = filteredBaseBreadcrumbs.map((item) => {
      if (item.path === '/knowledge-base') {
        return {
          ...item,
          onClick: (e?: React.MouseEvent) => {
            e?.preventDefault();
            e?.stopPropagation();
            
            if (options?.onKnowledgeBaseMenuClick) {
              // 如果提供了回调函数，执行回调
              options.onKnowledgeBaseMenuClick();
            } else if (breadcrumbType === 'detail') {
              // 知识库详情页面：没有回调函数时，返回到知识库列表页面
              navigate('/knowledge-base', {
                state: {
                  resetToRoot: true,
                }
              });
            }
            return false;
          },
        };
      }
      return item;
    });

    let customBreadcrumbs: MenuItem[] = [...breadcrumbsWithClick];

    if (breadcrumbType === 'list') {
      // 知识库列表页面：只显示知识库文件夹路径
      customBreadcrumbs = [
        ...breadcrumbsWithClick,
        ...breadcrumbPath.knowledgeBaseFolderPath.map((folder, index) => ({
          id: 0,
          parent: 0,
          code: null,
          label: folder.name,
          i18nKey: null,
          path: null,
          enable: true,
          display: true,
          level: 0,
          sort: 0,
          icon: null,
          iconActive: null,
          menuDesc: null,
          deleted: null,
          updateTime: 0,
          new_: null,
          keepAlive: false,
          master: null,
          disposable: false,
          appSystem: null,
          subs: [],
          onClick: (e?: React.MouseEvent) => {
            e?.preventDefault();
            e?.stopPropagation();
            
            // 如果有回调函数，直接调用回调函数来更新状态
            if (options?.onKnowledgeBaseFolderClick) {
              options.onKnowledgeBaseFolderClick(folder.id, breadcrumbPath.knowledgeBaseFolderPath.slice(0, index + 1));
            } else {
              // 否则使用导航（兜底逻辑）
              navigate('/knowledge-base', { 
                state: { 
                  navigateToFolder: folder.id,
                  folderPath: breadcrumbPath.knowledgeBaseFolderPath.slice(0, index + 1)
                } 
              });
            }
            return false;
          },
        })),
      ];
    } else {
      // 知识库详情页面：显示知识库名称 + 文档文件夹路径 + 文档名称
      customBreadcrumbs = [
        ...breadcrumbsWithClick,
        
        // 添加知识库名称
        ...(breadcrumbPath.knowledgeBase ? [{
          id: 0,
          parent: 0,
          code: null,
          label: breadcrumbPath.knowledgeBase.name,
          i18nKey: null,
          path: null,
          enable: true,
          display: true,
          level: 0,
          sort: 0,
          icon: null,
          iconActive: null,
          menuDesc: null,
          deleted: null,
          updateTime: 0,
          new_: null,
          keepAlive: false,
          master: null,
          disposable: false,
          appSystem: null,
          subs: [],
          onClick: (e?: React.MouseEvent) => {
            e?.preventDefault();
            e?.stopPropagation();
            // 返回到知识库详情页的根目录
            const navigationState = {
              fromKnowledgeBaseList: true,
              knowledgeBaseFolderPath: breadcrumbPath.knowledgeBaseFolderPath,
              resetToRoot: true, // 添加重置到根目录的标志
              refresh: true, // 添加刷新标志
              timestamp: Date.now(), // 添加时间戳确保状态变化
            };
            
            // 使用当前页面路径进行导航，避免不必要的路由变化
            const currentPath = window.location.pathname;
            const targetPath = `/knowledge-base/${breadcrumbPath.knowledgeBase!.id}/private`;
            
            if (currentPath === targetPath) {
              // 如果已经在目标页面，直接更新状态而不导航
              navigate(targetPath, { 
                state: navigationState,
                replace: true // 使用 replace 避免历史记录堆积
              });
            } else {
              // 如果不在目标页面，正常导航
              navigate(targetPath, { 
                state: navigationState
              });
            }
            return false;
          },
        }] : []),
        
        // 添加文档文件夹路径
        ...breadcrumbPath.documentFolderPath.map((folder, index) => ({
          id: 0,
          parent: 0,
          code: null,
          label: folder.name,
          i18nKey: null,
          path: null,
          enable: true,
          display: true,
          level: 0,
          sort: 0,
          icon: null,
          iconActive: null,
          menuDesc: null,
          deleted: null,
          updateTime: 0,
          new_: null,
          keepAlive: false,
          master: null,
          disposable: false,
          appSystem: null,
          subs: [],
          onClick: (e?: React.MouseEvent) => {
            e?.preventDefault();
            e?.stopPropagation();
            // 返回到知识库详情页的对应文件夹
            const navigationState = {
              fromKnowledgeBaseList: true,
              knowledgeBaseFolderPath: breadcrumbPath.knowledgeBaseFolderPath,
              navigateToDocumentFolder: folder.id,
              documentFolderPath: breadcrumbPath.documentFolderPath.slice(0, index + 1),
              refresh: true, // 添加刷新标志
              timestamp: Date.now(), // 添加时间戳确保状态变化
            };
            navigate(`/knowledge-base/${breadcrumbPath.knowledgeBase!.id}/private`, { 
              state: navigationState,
              replace: true // 使用 replace 避免历史记录堆积
            });
            return false;
          },
        })),
        
        // 添加文档名称（如果存在）
        ...(breadcrumbPath.document ? [{
          id: 0,
          parent: 0,
          code: null,
          label: breadcrumbPath.document.name,
          i18nKey: null,
          path: null,
          enable: true,
          display: true,
          level: 0,
          sort: 0,
          icon: null,
          iconActive: null,
          menuDesc: null,
          deleted: null,
          updateTime: 0,
          new_: null,
          keepAlive: false,
          master: null,
          disposable: false,
          appSystem: null,
          subs: [],
          // 文档名称不可点击
        }] : []),
      ];
    }

    // 根据面包屑类型使用不同的键，实现独立的面包屑路径
    const breadcrumbKey = breadcrumbType === 'list' ? 'space' : 'space-detail';
    

    
    setCustomBreadcrumbs(customBreadcrumbs, breadcrumbKey);
  }, [setCustomBreadcrumbs, navigate, options?.breadcrumbType, options?.onKnowledgeBaseMenuClick, options?.onKnowledgeBaseFolderClick]);

  return {
    updateBreadcrumbs,
  };
};