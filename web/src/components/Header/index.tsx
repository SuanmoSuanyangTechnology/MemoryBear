/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:07:49 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:07:49 
 */
/**
 * AppHeader Component
 * 
 * The main application header that displays breadcrumb navigation and user menu.
 * Supports different breadcrumb sources based on the current route.
 * 
 * @component
 */

import { type FC, useRef } from 'react';
import { Layout, Dropdown, Breadcrumb } from 'antd';
import type { MenuProps, BreadcrumbProps } from 'antd';
import { UserOutlined, LogoutOutlined, SettingOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { useLocation } from 'react-router-dom';

import { useUser } from '@/store/user';
import { useMenu } from '@/store/menu';
import styles from './index.module.css'
import SettingModal, { type SettingModalRef } from './SettingModal'
import UserInfoModal, { type UserInfoModalRef } from './UserInfoModal'

const { Header } = Layout;

/**
 * @param source - Breadcrumb source type ('space' or 'manage'), defaults to 'manage'
 */
const AppHeader: FC<{source?: 'space' | 'manage';}> = ({source = 'manage'}) => {
  const { t } = useTranslation();
  const location = useLocation();
  const settingModalRef = useRef<SettingModalRef>(null)
  const userInfoModalRef = useRef<UserInfoModalRef>(null)

  const { user, logout } = useUser();
  const { allBreadcrumbs } = useMenu();
  
  /**
   * Dynamically select breadcrumb source based on current route
   * - Knowledge base list: uses 'space' breadcrumb
   * - Knowledge base detail: uses 'space-detail' breadcrumb
   * - Other pages: uses the passed source prop
   */
  const getBreadcrumbSource = () => {
    const pathname = location.pathname;
    
    // Knowledge base list page uses default space breadcrumb
    if (pathname === '/knowledge-base') {
      return 'space';
    }
    
    // Knowledge base detail pages use independent breadcrumb
    if (pathname.includes('/knowledge-base/') && pathname !== '/knowledge-base') {
      return 'space-detail';
    }
    
    // Other pages use the passed source
    return source;
  };
  
  const breadcrumbSource = getBreadcrumbSource();
  const breadcrumbs = allBreadcrumbs[breadcrumbSource] || [];
  

  /** Handle user logout */
  const handleLogout = () => {
    logout()
  };

  /** User dropdown menu configuration with profile, settings, and logout options */
  const userMenuItems: MenuProps['items'] = [
    {
      key: '1',
      label: (<>
        <div>{user.username}</div>
        <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-2">{user.email}</div>
      </>),
    },
    {
      key: '2',
      type: 'divider',
    },
    {
      key: '3',
      icon: <UserOutlined />,
      label: t('header.userInfo'),
      onClick: () => {
        userInfoModalRef.current?.handleOpen()
      },
    },
    {
      key: '4',
      icon: <SettingOutlined />,
      label: t('header.settings'),
      onClick: () => {
        settingModalRef.current?.handleOpen()
      },
    },
    {
      key: '5',
      type: 'divider',
    },
    {
      key: '6',
      icon: <LogoutOutlined />,
      label: t('header.logout'),
      danger: true,
      onClick: handleLogout,
    },
  ];
  
  /**
   * Format breadcrumb items with proper titles, paths, and click handlers
   * - Translates i18n keys to display text
   * - Handles custom onClick events
   * - Disables navigation for the last breadcrumb item
   */
  const formatBreadcrumbNames = () => {
    return breadcrumbs.map((menu, index) => {
      const item: any = {
        title: menu.i18nKey ? t(menu.i18nKey) : menu.label,
      };
      
      // If it's the last item, don't set path
      if (index === breadcrumbs.length - 1) {
        return item;
      }
      
      // If has custom onClick, use onClick and set href to '#' to show pointer cursor
      if ((menu as any).onClick) {
        item.onClick = (e: React.MouseEvent) => {
          e.preventDefault();
          (menu as any).onClick(e);
        };
        item.href = '#';
      } else if (menu.path && menu.path !== '#') {
        // Only set path when path is not '#'
        item.path = menu.path;
      }
      
      return item;
    });
  }
  
  return (
    <Header className={styles.header}>
      {/* Breadcrumb navigation */}
      <Breadcrumb separator=">" items={formatBreadcrumbNames() as BreadcrumbProps['items']} />
      {/* User info dropdown menu */}
      <Dropdown
        menu={{
          items: userMenuItems
        }}
      >
        <div className="rb:cursor-pointer">{user.username}</div>
      </Dropdown>
      <SettingModal
        ref={settingModalRef}
      />
      <UserInfoModal
        ref={userInfoModalRef}
      />
    </Header>
  );
};

export default AppHeader;