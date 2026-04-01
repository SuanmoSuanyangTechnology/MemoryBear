/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:07:49 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-05 13:43:59
 */
/**
 * AppHeader Component
 * 
 * The main application header that displays breadcrumb navigation and user menu.
 * Supports different breadcrumb sources based on the current route.
 * 
 * @component
 */

import { type FC, useRef, useState } from 'react';
import { Layout, Dropdown, Breadcrumb, Flex } from 'antd';
import type { MenuProps, BreadcrumbProps } from 'antd';
import { useTranslation } from 'react-i18next';
import { useLocation } from 'react-router-dom';
import clsx from 'clsx';

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
      icon: <Flex align="center" justify="center" className="rb:size-10 rb:rounded-xl rb:bg-[#155EEF] rb:text-white">
        {/[\u4e00-\u9fa5]/.test(user.username) ? user.username.slice(0, 2) : user.username[0]}
      </Flex>,
      label: (<>
        <div className="rb:text-[#212332] rb:leading-5">{user.username}</div>
        <div className="rb:text-[12px] rb:text-[#7B8085] rb:leading-4.5 rb:mt-0.5 rb:mr-2">{user.email}</div>
      </>),
    },
    {
      key: '2',
      type: 'divider',
      className: 'rb:bg-[#EBEBEB]!'
    },
    {
      key: '3',
      icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('src/assets/images/menuNew/userInfo.svg')]"></div>,
      label: <Flex justify="space-between" align="center">
        {t('header.userInfo')}
        <div className="rb:size-4 rb:bg-cover rb:bg-[url('src/assets/images/menuNew/arrow_t_r.svg')]"></div>
      </Flex>,
      className: 'rb:text-[#212332]!',
      onClick: () => {
        userInfoModalRef.current?.handleOpen()
      },
    },
    {
      key: '4',
      icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('src/assets/images/menuNew/settings.svg')]"></div>,
      label: <Flex justify="space-between" align="center">
        {t('header.settings')}
        <div className="rb:size-4 rb:bg-cover rb:bg-[url('src/assets/images/menuNew/arrow_t_r.svg')]"></div>
      </Flex>,
      className: 'rb:text-[#212332]!',
      onClick: () => {
        settingModalRef.current?.handleOpen()
      },
    },
    {
      key: '5',
      type: 'divider',
      className: 'rb:bg-[#EBEBEB]!'
    },
    {
      key: '6',
      icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('src/assets/images/menuNew/logout_red.svg')]"></div>,
      label: t('header.logout'),
      danger: true,
      className: 'rb:hover:rb:bg-transparent rb:hover:text-[#FF5D34]!',
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
    return breadcrumbs.filter(item => item.type !== 'group').map((menu, index) => {
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
  
  const [open, setOpen] = useState(false);
  const handleOpenChange = (open: boolean) => {
    setOpen(open);
  }
  return (
    <Header className={styles.header}>
      {/* Breadcrumb navigation */}
      <Breadcrumb separator="<" items={formatBreadcrumbNames() as BreadcrumbProps['items']} className="rb:font-medium!" />
      {/* User info dropdown menu */}
      {user.username && (
        <Dropdown
          menu={{
            items: userMenuItems
          }}
          onOpenChange={handleOpenChange}
          overlayClassName={styles.userDropdown}
        >
          <Flex align="center" className="rb:cursor-pointer rb:font-medium">
            <Flex align="center" justify="center" className="rb:size-8 rb:rounded-xl rb:bg-[#155EEF] rb:text-white rb:mr-2!">
              {/[\u4e00-\u9fa5]/.test(user.username) ? user.username.slice(0, 2) : user.username[0]}
            </Flex>
            <span className="rb:text-[#212332] rb:text-[12px] rb:leading-4 rb:mr-1">{user.username}</span>
            <div className={clsx("rb:size-3 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')]", {
              'rb:rotate-180': !open,
            })}></div>
          </Flex>
        </Dropdown>
      )}
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