/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:07:49 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-16 10:31:21
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
import { Layout, Dropdown, Breadcrumb, Flex, Tooltip } from 'antd';
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
const AppHeader: FC<{ source?: 'space' | 'manage'; }> = ({ source = 'manage' }) => {
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
      icon: user.username
        ? <Flex align="center" justify="center" className="rb:size-10 rb:rounded-xl rb:bg-[#155EEF] rb:text-white">
          {/[\u4e00-\u9fa5]/.test(user.username) ? user.username.slice(-2) : user.username[0]}
        </Flex>
        : null,
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
      icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/menuNew/userInfo.svg')]"></div>,
      label: <Flex justify="space-between" align="center">
        {t('header.userInfo')}
        <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/menuNew/arrow_t_r.svg')]"></div>
      </Flex>,
      className: 'rb:text-[#212332]!',
      onClick: () => {
        userInfoModalRef.current?.handleOpen()
      },
    },
    {
      key: '4',
      icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/menuNew/settings.svg')]"></div>,
      label: <Flex justify="space-between" align="center">
        {t('header.settings')}
        <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/menuNew/arrow_t_r.svg')]"></div>
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
      icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/menuNew/logout_red.svg')]"></div>,
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
    const filtered = breadcrumbs.filter(item => item.type !== 'group');
    return filtered.map((menu, index) => {
      const label = menu.i18nKey ? t(menu.i18nKey) : menu.label;
      const isLast = index === filtered.length - 1;
      const item: any = {
        title: (
          <Tooltip title={label} placement="bottom">
            <span className={styles.breadcrumbTitle}>{label}</span>
          </Tooltip>
        ),
      };

      if (!isLast) {
        if ((menu as any).onClick) {
          item.onClick = (e: React.MouseEvent) => {
            e.preventDefault();
            (menu as any).onClick(e);
          };
          item.href = '#';
        } else if (menu.path && menu.path !== '#') {
          item.path = menu.path;
        }
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
            {user.username && <Flex align="center" justify="center" className="rb:size-8 rb:rounded-xl rb:bg-[#155EEF] rb:text-white rb:mr-2!">
              {/[\u4e00-\u9fa5]/.test(user.username) ? user.username.slice(-2) : user.username[0]}
            </Flex>}
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