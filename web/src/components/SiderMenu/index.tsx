/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:25:31 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:25:31 
 */
/**
 * SiderMenu Component
 * 
 * A collapsible sidebar navigation menu with:
 * - Dynamic menu generation from configuration
 * - Active state management with icon switching
 * - Nested submenu support
 * - Workspace/space context switching
 * - Role-based menu filtering
 * - Internationalization support
 * 
 * @component
 */

import { useState, useEffect, type FC } from 'react';
import { Menu as AntMenu, Layout } from 'antd';
import { UserOutlined } from '@ant-design/icons';
import type { MenuProps } from 'antd';
import { useNavigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import { useMenu, type MenuItem } from '@/store/menu';
import styles from './index.module.css'
import logo from '@/assets/images/logo.png'
import menuFold from '@/assets/images/menuFold.png'
import menuUnfold from '@/assets/images/menuUnfold.png'
import { useUser } from '@/store/user';
import logout from '@/assets/images/logout.svg'

// Import SVG files
import dashboardIcon from '@/assets/images/menu/dashboard.svg';
import dashboardActiveIcon from '@/assets/images/menu/dashboard_active.svg';
import modelIcon from '@/assets/images/menu/model.svg';
import modelActiveIcon from '@/assets/images/menu/model_active.svg';
import memoryIcon from '@/assets/images/menu/memory.svg';
import memoryActiveIcon from '@/assets/images/menu/memory_active.svg';
import spaceIcon from '@/assets/images/menu/space.svg';
import spaceActiveIcon from '@/assets/images/menu/space_active.svg';
import userIcon from '@/assets/images/menu/user.svg';
import userActiveIcon from '@/assets/images/menu/user_active.svg';
import userMemoryIcon from '@/assets/images/menu/userMemory.svg';
import userMemoryActiveIcon from '@/assets/images/menu/userMemory_active.svg';
import applicationIcon from '@/assets/images/menu/application.svg';
import applicationActiveIcon from '@/assets/images/menu/application_active.svg';
import knowledgeIcon from '@/assets/images/menu/knowledge.svg';
import knowledgeActiveIcon from '@/assets/images/menu/knowledge_active.svg';
import memoryConversationIcon from '@/assets/images/menu/memoryConversation.svg';
import memoryConversationActiveIcon from '@/assets/images/menu/memoryConversation_active.svg';
import memberIcon from '@/assets/images/menu/member.svg';
import memberActiveIcon from '@/assets/images/menu/member_active.svg';
import toolIcon from '@/assets/images/menu/tool.png';
import toolActiveIcon from '@/assets/images/menu/tool_active.png';
import apiKeyIcon from '@/assets/images/menu/apiKey.png';
import apiKeyActiveIcon from '@/assets/images/menu/apiKey_active.png';
import pricingIcon from '@/assets/images/menu/pricing.svg'
import pricingActiveIcon from '@/assets/images/menu/pricing_active.svg'
import spaceConfigIcon from '@/assets/images/menu/spaceConfig.svg'
import spaceConfigActiveIcon from '@/assets/images/menu/spaceConfig_active.svg'
import ontologyIcon from '@/assets/images/menu/ontology.svg'
import ontologyActiveIcon from '@/assets/images/menu/ontology_active.svg'
import promptIcon from '@/assets/images/menu/prompt.svg'
import promptActiveIcon from '@/assets/images/menu/prompt_active.svg'

/** Icon path mapping table for menu items (normal and active states) */
const iconPathMap: Record<string, string> = {
  'dashboard': dashboardIcon,
  'dashboardActive': dashboardActiveIcon,
  'model': modelIcon,
  'modelActive': modelActiveIcon,
  'memory': memoryIcon,
  'memoryActive': memoryActiveIcon,
  'space': spaceIcon,
  'spaceActive': spaceActiveIcon,
  'user': userIcon,
  'userActive': userActiveIcon,
  'userMemory': userMemoryIcon,
  'userMemoryActive': userMemoryActiveIcon,
  'application': applicationIcon,
  'applicationActive': applicationActiveIcon,
  'knowledge': knowledgeIcon,
  'knowledgeActive': knowledgeActiveIcon,
  'memoryConversation': memoryConversationIcon,
  'memoryConversationActive': memoryConversationActiveIcon,
  'member': memberIcon,
  'memberActive': memberActiveIcon,
  'tool': toolIcon,
  'toolActive': toolActiveIcon,
  'apiKey': apiKeyIcon,
  'apiKeyActive': apiKeyActiveIcon,
  'pricing': pricingIcon,
  'pricingActive': pricingActiveIcon,
  'spaceConfig': spaceConfigIcon,
  'spaceConfigActive': spaceConfigActiveIcon,
  'ontology': ontologyIcon,
  'ontologyActive': ontologyActiveIcon,
  'prompt': promptIcon,
  'promptActive': promptActiveIcon,
};

const { Sider } = Layout;

/** Sidebar menu component with collapsible navigation */
const Menu: FC<{
  /** Menu display mode */
  mode?: 'vertical' | 'horizontal' | 'inline';
  /** Menu context (space or manage) */
  source?: 'space' | 'manage';
}> = ({ mode = 'inline', source = 'manage' }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { t } = useTranslation();
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const { allMenus, collapsed, loadMenus, toggleSider } = useMenu()
  const [menus, setMenus] = useState<MenuItem[]>([])
  const { user, storageType } = useUser()

  /** Filter menus based on user role and source */
  useEffect(() => {
    if (user.role === 'member' && source === 'space') {
      setMenus((allMenus[source] || []).filter(menu => menu.code !== 'member'))
    } else if (user) {
      setMenus(allMenus[source] || [])
    }
  }, [source, allMenus, user])
  
  /** Handle menu item click and navigate to path */
  const handleMenuClick: MenuProps['onClick'] = (e) => {
    const path = e.key;
    if (path) {
      navigate(path);
      setSelectedKeys([path]);
    }
  };

  /** Convert custom menu format to Ant Design Menu items format */
  const generateMenuItems = (menuList: MenuItem[]): MenuProps['items'] => {

    return menuList.filter(menu => menu.display).map((menu) => {
      const iconKey = selectedKeys.includes(menu.path || '') ? `${menu.code}Active` : menu.code;
      const iconSrc = iconPathMap[iconKey as keyof typeof iconPathMap];
      const subs = (menu.subs || []).filter(sub => sub.display);
      /** Leaf node - menu item without children */
      if (!subs || subs.length === 0) {
        if (!menu.path) return null;

        return {
          key: menu.path,
          title: menu.i18nKey ? t(menu.i18nKey) : menu.label,
          label: (
            <span data-menu-id={menu.path}>
              {menu.i18nKey ? t(menu.i18nKey) : menu.label}
            </span>
          ),
          icon: iconSrc ? <img
            src={iconSrc}
            className="rb:w-4 rb:h-4 rb:mr-2"
          /> : null,
        };
      }

      /** Node with submenu - menu item with children */
      const menuLabel = menu.i18nKey ? t(menu.i18nKey) : menu.label;
      return {
        key: `submenu-${menu.id}`,
        title: menuLabel,
        label: menuLabel,
        icon: iconSrc ? <img
          src={iconSrc}
          className="rb:w-4 rb:h-4 rb:mr-2"
        /> : <UserOutlined/>,
        children: generateMenuItems(subs),
      };
    }).filter(Boolean);
  };

  /** Generate menu items from configuration */
  const menuItems = generateMenuItems(menus);
  
  /** Load menus on component mount */
  useEffect(() => {
    loadMenus(source);
  }, [])

  /** Handle current path matching and update selected keys */
  useEffect(() => {
    /** Use location.pathname to get current path, ensuring consistency with routing system */
    const currentPath = location.pathname || '/';

    /** Try to find matching menu item and corresponding parent menu path */
    const findMatchingKey = (menuList: MenuItem[], parentPaths: string[] = []): { key: string | null; } => {
      for (const menu of menuList) {
        if (menu.path) {
          const menuPath = menu.path[0] !== '/' ? '/' + menu.path : menu.path;

          /** Exact match or path prefix match (ensure complete path segment match) */
          const isExactMatch = menuPath === currentPath;
          const isPrefixMatch = currentPath.startsWith(menuPath + '/') ||
            currentPath === menuPath;

          if (isExactMatch || isPrefixMatch) {
            return { key: menu.path };
          }
        }

        /** Recursively check submenus */
        if (menu.subs && menu.subs.length > 0) {
          const newParentPaths = [...parentPaths, `submenu-${menu.id}`];
          const found = findMatchingKey(menu.subs, newParentPaths);
          if (found.key) {
            return found;
          }
        }
      }
      return { key: null };
    };

    const { key: matchingKey } = findMatchingKey(menus);
    if (matchingKey) {
      setSelectedKeys([matchingKey]);
    } else {
      setSelectedKeys([])
    }
  }, [menus, location.pathname]);

  /** Navigate to space list and clear user cache */
  const goToSpace = () => {
    navigate('/space')
    localStorage.removeItem('user')
  }

  return (
    <Sider
      width={240}
      collapsedWidth={64}
      collapsed={collapsed}
      className={styles.sider}
    >
      {/* Sidebar header with logo/workspace name and collapse toggle */}
      <div className={clsx(styles.title, {
        [styles.collapsed]: collapsed,
        'rb:flex rb:items-center rb:text-[14px]! rb:py-2!': !collapsed && source === 'space' && user.current_workspace_name,
      })}>
        {!collapsed && source === 'space' && user.current_workspace_name
          ? <div className="rb:w-43.75 rb:text-center">
            <div className="rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{user.current_workspace_name}</div>
            <span className="rb:text-[12px] rb:text-[#5B6167] rb:leading-4 rb:font-regular">
              {t(`space.${storageType}`)}
            </span>
          </div>
          : !collapsed
            ? <div className="rb:flex">
              <img src={logo} className={styles.logo} />
              {t('title')}
            </div>
            : null
        }
        <img src={collapsed ? menuUnfold : menuFold} className={styles.menuIcon} onClick={toggleSider} />
      </div>
      {/* Main navigation menu */}
      <AntMenu
        style={{ borderRight: 0 }}
        mode={mode}
        selectedKeys={selectedKeys}
        // openKeys={openKeys}
        onClick={handleMenuClick}
        items={menuItems}
        inlineCollapsed={collapsed}
        inlineIndent={13}
        className="rb:max-h-[calc(100vh-136px)] rb:overflow-y-auto"
      />
      {/* Return to space button for superusers */}
      {user?.is_superuser && source === 'space' &&
        <div
          onClick={goToSpace}
          className="rb:pl-6.25 rb:flex rb:items-center rb:justify-start rb:absolute rb:bottom-8 rb:w-full rb:text-[12px] rb:text-[#5B6167] rb:hover:text-[#212332] rb:leading-4 rb:font-regular rb:text-center rb:mt-6 rb:cursor-pointer"
        >
          <img src={logout} className="rb:w-4 rb:h-4 rb:mr-4" />
          {collapsed ? null : t('common.returnToSpace')}
        </div>
      }
    </Sider>
  );
};

export default Menu;