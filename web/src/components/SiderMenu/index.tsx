/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:25:31 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-08 16:21:25
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

import { UserOutlined } from '@ant-design/icons';
import type { MenuProps } from 'antd';
import { Menu as AntMenu, Divider, Flex, Layout } from 'antd';
import clsx from 'clsx';
import { useEffect, useRef, useState, type FC } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate } from 'react-router-dom';

import { getTenantSubscription } from '@/api/user';
import logo from '@/assets/images/logo.png';
import { useI18n } from '@/store/locale';
import { useMenu, type MenuItem } from '@/store/menu';
import { useUser } from '@/store/user';
import styles from './index.module.css';
import SubscriptionDetailModal, { type SubscriptionDetailModalRef } from './SubscriptionDetailModal';
import SwitchSpaceModal, { type SwitchSpaceModalRef } from './SwitchSpaceModal';
import { formatDateTime } from '@/utils/format'

// Import SVG files
// space
import apiKeyIcon from '@/assets/images/menuNew/apiKey.svg';
import apiKeyActiveIcon from '@/assets/images/menuNew/apiKey_active.svg';
import applicationIcon from '@/assets/images/menuNew/application.svg';
import applicationActiveIcon from '@/assets/images/menuNew/application_active.svg';
import dashboardIcon from '@/assets/images/menuNew/dashboard.svg';
import dashboardActiveIcon from '@/assets/images/menuNew/dashboard_active.svg';
import knowledgeIcon from '@/assets/images/menuNew/knowledge.svg';
import knowledgeActiveIcon from '@/assets/images/menuNew/knowledge_active.svg';
import memberIcon from '@/assets/images/menuNew/member.svg';
import memberActiveIcon from '@/assets/images/menuNew/member_active.svg';
import memoryIcon from '@/assets/images/menuNew/memory.svg';
import memoryActiveIcon from '@/assets/images/menuNew/memory_active.svg';
import memoryConversationIcon from '@/assets/images/menuNew/memoryConversation.svg';
import memoryConversationActiveIcon from '@/assets/images/menuNew/memoryConversation_active.svg';
import ontologyIcon from '@/assets/images/menuNew/ontology.svg';
import ontologyActiveIcon from '@/assets/images/menuNew/ontology_active.svg';
import promptIcon from '@/assets/images/menuNew/prompt.svg';
import promptActiveIcon from '@/assets/images/menuNew/prompt_active.svg';
import spaceConfigIcon from '@/assets/images/menuNew/spaceConfig.svg';
import spaceConfigActiveIcon from '@/assets/images/menuNew/spaceConfig_active.svg';
import userMemoryIcon from '@/assets/images/menuNew/userMemory.svg';
import userMemoryActiveIcon from '@/assets/images/menuNew/userMemory_active.svg';

// manage
import modelIcon from '@/assets/images/menuNew/model.svg';
import modelActiveIcon from '@/assets/images/menuNew/model_active.svg';
import pricingIcon from '@/assets/images/menuNew/pricing.svg';
import pricingActiveIcon from '@/assets/images/menuNew/pricing_active.svg';
import skillsIcon from '@/assets/images/menuNew/skills.svg';
import skillsActiveIcon from '@/assets/images/menuNew/skills_active.svg';
import spaceIcon from '@/assets/images/menuNew/space.svg';
import spaceActiveIcon from '@/assets/images/menuNew/space_active.svg';
import toolIcon from '@/assets/images/menuNew/tool.svg';
import toolActiveIcon from '@/assets/images/menuNew/tool_active.svg';
import userIcon from '@/assets/images/menuNew/user.svg';
import userActiveIcon from '@/assets/images/menuNew/user_active.svg';

export interface PackagePlan {
  id: string
  name: string
  name_en?: string
  version: string
  category: string
  tier_level: number
  price: number
  billing_cycle: string
  core_value?: string
  core_value_en?: string
  tech_support?: string
  tech_support_en?: string
  sla_compliance?: string
  sla_compliance_en?: string
  page_customization?: string
  page_customization_en?: string
  theme_color?: string
}

export interface SubscriptionQuota {
  app_quota: number
  model_quota: number
  skill_quota: number
  end_user_quota: number
  workspace_quota: number
  api_ops_rate_limit: number
  memory_engine_quota: number
  ontology_project_quota: number
  knowledge_capacity_quota: number
}

export interface Subscription {
  subscription_id: string | null
  tenant_id: string
  package_plan_id: string
  package_version: string
  package_plan: PackagePlan
  started_at: number | null
  expired_at: number | null
  status: string
  quotas: SubscriptionQuota
  created_at: number
  updated_at: number
}
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
  'skills': skillsIcon,
  'skillsActive': skillsActiveIcon,
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
  const { language } = useI18n()
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const { allMenus, collapsed, loadMenus, toggleSider } = useMenu()
  const [menus, setMenus] = useState<MenuItem[]>([])
  const { user, storageType } = useUser()
  const subscriptionDetailRef = useRef<SubscriptionDetailModalRef>(null)
  const switchSpaceModalRef = useRef<SwitchSpaceModalRef>(null)

  /** Filter menus based on user role and source */
  useEffect(() => {
    if (!user) return
    let menuList: MenuItem[] = []
    
    if (user.role === 'member' && source === 'space') {
      menuList = (allMenus[source] || []).filter(menu => menu.code !== 'member')
    } else if (user) {
      menuList = allMenus[source] || []
    }

    const noAuthList = ['user', 'pricing'].filter(vo => (Array.isArray(user.permissions) && !user.permissions?.includes(vo) && !user.permissions?.includes('all')) || !Array.isArray(user.permissions))

    if (noAuthList && !noAuthList?.includes('all')) {
      const filterMenus = (list: MenuItem[]): MenuItem[] =>{
        const filterList = list?.filter(menu => !noAuthList?.includes(menu.code as string))

        const showList: MenuItem[] = [] 
        filterList?.forEach(menu => {
          const filteredSubs = filterMenus(menu.subs || [])
          const hadSubs = menu.subs && menu.subs.length > 0
          if (hadSubs && filteredSubs.length === 0) return
          if (menu.type === 'group' && (!menu.subs || menu.subs?.length < 1)) return
          showList.push({ ...menu, subs: filteredSubs })
        })

        return showList
      }
      menuList = filterMenus(menuList)
    }

    setMenus(menuList)
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
    const items: MenuProps['items'] = [];
    const filteredMenus = menuList.filter(menu => menu.display);

    filteredMenus.forEach((menu, index) => {
      const iconKey = selectedKeys.includes(menu.path || '') ? `${menu.code}Active` : menu.code;
      const iconSrc = iconPathMap[iconKey as keyof typeof iconPathMap];
      const subs = (menu.subs || []).filter(sub => sub.display);

      /** Leaf node - menu item without children */
      if (!subs || subs.length === 0) {
        if (menu.path) {
          items.push({
            key: menu.path,
            title: menu.i18nKey ? t(menu.i18nKey) : menu.label,
            label: (
              <span data-menu-id={menu.path}>
                {menu.i18nKey ? t(menu.i18nKey) : menu.label}
              </span>
            ),
            icon: iconSrc ? <img
              src={iconSrc}
              className="rb:w-4 rb:h-4"
              alt={iconSrc}
            /> : null,
          });
        }
      } else {
        /** Node with submenu - menu item with children */
        const menuLabel = collapsed && menu.type === 'group'? '':  menu.i18nKey ? t(menu.i18nKey) : menu.label;
        const children = generateMenuItems(subs) || [];
        items.push({
          key: `submenu-${menu.id}`,
          ...(menu.type === 'group' ? { type: 'group' as const } : {}),
          title: menuLabel,
          label: menuLabel,
          icon: iconSrc ? <img
            src={iconSrc}
            className="rb:w-4 rb:h-4"
            alt={iconSrc}
          /> : <UserOutlined/>,
          children,
        });
      }

      /** Add divider after group items (except the last one) */
      if (menu.type === 'group' && index < filteredMenus.length - 1) {
        items.push({ type: 'divider', key: `divider-${menu.id}` });
      }
    });

    return items;
  };

  /** Generate menu items from configuration */
  const menuItems = generateMenuItems(menus);
  
  /** Load menus on component mount */
  useEffect(() => {
    loadMenus(source);
  }, [loadMenus, source])

  /** Handle current path matching and update selected keys */
  useEffect(() => {
    /** Use location.pathname to get current path, ensuring consistency with routing system */
    const currentPath = location.pathname || '/';

    /** Try to find matching menu item and corresponding parent menu path */
    const findMatchingKey = (menuList: MenuItem[], parentPaths: string[] = []): { key: string | null; } => {
      for (const menu of menuList) {
        if (menu.path) {
          const menuPath = menu.path?.[0] !== '/' ? '/' + menu.path : menu.path;

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

  const [subscription, setSubscription] = useState<Subscription | null>(null)
  useEffect(() => {
    if (source === 'manage') {
      getSubscription()
    } else {
      setSubscription(null)
    }
  }, [source])

  const getSubscription = () => {
    return new Promise((resolve, reject) => {
      getTenantSubscription()
        .then(res => {
          setSubscription(res as Subscription)
          resolve(res)
        })
        .catch((error) => {
          reject(error)
        })
    })
  }

  const getKeyWithLanguage = (key: string) => {
    return (language === 'en' ? `${key}_en` : key) as keyof Subscription['package_plan']
  }
  const handleViewDetail = () => {
    getSubscription()
      .then(res => {
        subscriptionDetailRef.current?.handleOpen(res as Subscription)
      })
      .catch(() => {
        subscriptionDetailRef.current?.handleOpen(subscription)
      })
  }
  const handleSwitchSpace = () => {
    switchSpaceModalRef.current?.handleOpen()
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
          ? <Flex gap={9}>
            <Flex align="center" justify="center" className="rb:size-10 rb:rounded-xl rb:bg-[#171719] rb:text-white rb:text-[18px] rb:font-medium">{user.current_workspace_name[0]}</Flex>
            <div className="rb:w-32">
              <div className="rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap rb:font-medium rb:text-[16px] rb:leading-5.5">{user.current_workspace_name}</div>
              <span className="rb:text-[14px] rb:text-[#5B6167] rb:leading-5 rb:font-regular">
                {t(`space.${storageType}`)}
              </span>
            </div>
          </Flex>
          : !collapsed
            ? <Flex>
              <img src={logo} className={styles.logo}
                alt={logo} />
              {t('title')}
            </Flex>
            : null
        }
        <div className={clsx("rb:cursor-pointer rb:size-5 rb:bg-cover rb:bg-[url('@/assets/images/menuNew/menuFold.svg')]", {
          'rb:rotate-180': collapsed
        })} onClick={toggleSider}></div>
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
        inlineIndent={10}
        className="rb:overflow-y-auto rb:flex-1!"
      />
      {/* Return to space button for superusers */}
      {source === 'space' &&
        <Flex gap={4} vertical className="rb:my-3! rb:mx-3!">
          <Divider className="rb:mb-2.5! rb:mt-0! rb:border-[#DFE4ED]! rb:mx-2! rb:min-w-[calc(100%-20px)]! rb:w-[calc(100%-20px)]!" />
          <Flex
            gap={8}
            align="center"
            justify="start"
            onClick={handleSwitchSpace}
            className="rb:p-2.5! rb:text-[13px] rb:hover:bg-[rgba(223,228,237,0.5)] rb:rounded-lg rb:leading-3.5 rb:font-regular rb:text-center rb:cursor-pointer"
          >
            <div className="rb:cursor-pointer rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/menuNew/switch.svg')]"></div>
            {collapsed ? null : t('common.switchSpace')}
          </Flex>
          {user?.is_superuser &&
            <Flex
              gap={8}
              align="center"
              justify="start"
              onClick={goToSpace}
              className="rb:p-2.5! rb:text-[13px] rb:hover:bg-[rgba(223,228,237,0.5)] rb:rounded-lg rb:leading-3.5 rb:font-regular rb:text-center rb:cursor-pointer"
            >
              <div className="rb:cursor-pointer rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/menuNew/return.svg')]"></div>
              {collapsed ? null : t('common.returnToSpace')}
            </Flex>
          }
        </Flex>
      }
      {source === 'manage' && subscription && !collapsed &&
        <div className="rb:mb-3 rb:ml-3 rb:mr-3 rb:py-3 rb:bg-cover rb:bg-[url('@/assets/images/menuNew/package_bg.png')] rb:overflow-hidden rb:rounded-xl">
          <Flex align="center" justify="space-between" className="rb:px-3!">
            <div className="rb:h-4.5 rb:flex-1 rb:truncate rb:text-[13px] rb:font-medium rb:leading-4.5">
              {subscription.package_plan?.[getKeyWithLanguage('name')]}
            </div>

            {subscription.expired_at &&
              <div className="rb:text-[10px] rb:text-[#5B6167]">
                {formatDateTime(subscription.expired_at, 'YYYY-MM-DD')}
                {t('package.expired')}
              </div>
            }
          </Flex>

          <div className="rb:grid rb:grid-cols-4 rb:mt-4">
            {['workspace_quota', 'skill_quota', 'app_quota', 'model_quota'].map(key => (
              <div key={key} className="rb:text-center">
                <div className="rb:text-[13px] rb:font-[MiSans-Semibold] rb:font-semibold">{subscription.quotas?.[key as keyof typeof subscription.quotas] ?? t('package.noLimit')}</div>
                <div className="rb:mt-1 rb:text-[#5B6167] rb:text-[10px] rb:leading-3.5">{t(`index.${key}`)}</div>
              </div>
            ))}
          </div>
          <Flex align="center" justify="center" className="rb:mt-4! rb:border rb:p-2! rb:text-[12px] rb:leading-4 rb:mx-3! rb:rounded-lg rb:cursor-pointer"
            onClick={handleViewDetail}
          >
            {t('package.viewDetail')}
            <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/index/arrow_right_dark.svg')]"></div>
          </Flex>
        </div>
      }

      <SubscriptionDetailModal
        ref={subscriptionDetailRef}
        currentPackage={subscription}
      />
      <SwitchSpaceModal
        ref={switchSpaceModalRef}
      />
    </Sider>
  );
};

export default Menu;