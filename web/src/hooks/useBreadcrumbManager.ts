/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:24:44 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 16:24:44 
 */
/**
 * useBreadcrumbManager Hook
 * 
 * Manages breadcrumb navigation for knowledge base pages with:
 * - Dynamic breadcrumb generation based on folder/document paths
 * - Separate breadcrumb handling for list and detail views
 * - Click handlers for navigation between folders
 * - Support for custom callbacks
 * 
 * @hook
 */

import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMenu } from '@/store/menu';
import type { MenuItem } from '@/store/menu';

/** Breadcrumb item interface */
export interface BreadcrumbItem {
  id: string;
  name: string;
  type?: 'knowledgeBase' | 'folder' | 'document';
}

/** Breadcrumb path structure */
export interface BreadcrumbPath {
  /** Knowledge base folder path */
  knowledgeBaseFolderPath: BreadcrumbItem[];
  /** Knowledge base information */
  knowledgeBase?: BreadcrumbItem;
  /** Document folder path */
  documentFolderPath: BreadcrumbItem[];
  /** Document information */
  document?: BreadcrumbItem;
}

/** Options for breadcrumb manager */
export interface BreadcrumbOptions {
  /** Callback when knowledge base menu is clicked */
  onKnowledgeBaseMenuClick?: () => void;
  /** Callback when knowledge base folder is clicked */
  onKnowledgeBaseFolderClick?: (folderId: string, folderPath: BreadcrumbItem[]) => void;
  /** Breadcrumb type: list or detail view */
  breadcrumbType?: 'list' | 'detail';
}

export const useBreadcrumbManager = (options?: BreadcrumbOptions) => {
  const { allBreadcrumbs, setCustomBreadcrumbs } = useMenu();
  const navigate = useNavigate();

  /** Update breadcrumbs based on current path and type */
  const updateBreadcrumbs = useCallback((breadcrumbPath: BreadcrumbPath) => {
    const breadcrumbType = options?.breadcrumbType || 'list';
    
    /** For detail pages, use fixed knowledge base breadcrumb */
    let baseBreadcrumbs: MenuItem[] = [];
    
    if (breadcrumbType === 'detail') {
      /** Detail page: always use fixed knowledge base management breadcrumb */
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
      /** List page: get base breadcrumbs from space, ensure knowledge base management is included */
      const spaceBreadcrumbs = allBreadcrumbs['space'] || [];
      const knowledgeBaseMenuIndex = spaceBreadcrumbs.findIndex(item => item.path === '/knowledge-base');
      
      if (knowledgeBaseMenuIndex >= 0) {
        baseBreadcrumbs = spaceBreadcrumbs.slice(0, knowledgeBaseMenuIndex + 1);
      } else {
        /** If knowledge base menu not found, use default knowledge base management breadcrumb */
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

    /** Add click event to "Knowledge Base Management" */
    const breadcrumbsWithClick = filteredBaseBreadcrumbs.map((item) => {
      if (item.path === '/knowledge-base') {
        return {
          ...item,
          onClick: (e?: React.MouseEvent) => {
            e?.preventDefault();
            e?.stopPropagation();
            
            if (options?.onKnowledgeBaseMenuClick) {
              /** If callback provided, execute callback */
              options.onKnowledgeBaseMenuClick();
            } else if (breadcrumbType === 'detail') {
              /** Knowledge base detail page: return to knowledge base list page when no callback */
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
      /** Knowledge base list page: only show knowledge base folder path */
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
            
            /** If callback provided, call callback to update state */
            if (options?.onKnowledgeBaseFolderClick) {
              options.onKnowledgeBaseFolderClick(folder.id, breadcrumbPath.knowledgeBaseFolderPath.slice(0, index + 1));
            } else {
              /** Otherwise use navigation (fallback logic) */
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
      /** Knowledge base detail page: show knowledge base name + document folder path + document name */
      customBreadcrumbs = [
        ...breadcrumbsWithClick,
        
        /** Add knowledge base name */
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
            /** Return to knowledge base detail page root directory */
            const navigationState = {
              fromKnowledgeBaseList: true,
              knowledgeBaseFolderPath: breadcrumbPath.knowledgeBaseFolderPath,
              resetToRoot: true, /** Add flag to reset to root directory */
              refresh: true, /** Add refresh flag */
              timestamp: Date.now(), /** Add timestamp to ensure state change */
            };
            
            /** Use current page path for navigation to avoid unnecessary route changes */
            const currentPath = window.location.pathname;
            const targetPath = `/knowledge-base/${breadcrumbPath.knowledgeBase!.id}/private`;
            
            if (currentPath === targetPath) {
              /** If already on target page, update state directly without navigation */
              navigate(targetPath, { 
                state: navigationState,
                replace: true /** Use replace to avoid history stack buildup */
              });
            } else {
              /** If not on target page, navigate normally */
              navigate(targetPath, { 
                state: navigationState
              });
            }
            return false;
          },
        }] : []),
        
        /** Add document folder path */
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
            /** Return to corresponding folder in knowledge base detail page */
            const navigationState = {
              fromKnowledgeBaseList: true,
              knowledgeBaseFolderPath: breadcrumbPath.knowledgeBaseFolderPath,
              navigateToDocumentFolder: folder.id,
              documentFolderPath: breadcrumbPath.documentFolderPath.slice(0, index + 1),
              refresh: true, /** Add refresh flag */
              timestamp: Date.now(), /** Add timestamp to ensure state change */
            };
            navigate(`/knowledge-base/${breadcrumbPath.knowledgeBase!.id}/private`, { 
              state: navigationState,
              replace: true /** Use replace to avoid history stack buildup */
            });
            return false;
          },
        })),
        
        /** Add document name (if exists) */
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
          /** Document name is not clickable */
        }] : []),
      ];
    }

    /** Use different keys based on breadcrumb type to implement independent breadcrumb paths */
    const breadcrumbKey = breadcrumbType === 'list' ? 'space' : 'space-detail';
    

    
    setCustomBreadcrumbs(customBreadcrumbs, breadcrumbKey);
  }, [setCustomBreadcrumbs, navigate, options?.breadcrumbType, options?.onKnowledgeBaseMenuClick, options?.onKnowledgeBaseFolderClick]);

  return {
    updateBreadcrumbs,
  };
};