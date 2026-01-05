import { useState, useEffect, type FC } from 'react';
import { Menu as AntMenu, Layout } from 'antd';
import { UserOutlined } from '@ant-design/icons';
import type { MenuProps } from 'antd';
import { useNavigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMenu, type MenuItem } from '@/store/menu';
import styles from './index.module.css'
import logo from '@/assets/images/logo.png'
import menuFold from '@/assets/images/menuFold.png'
import menuUnfold from '@/assets/images/menuUnfold.png'
import clsx from 'clsx';
import { useUser } from '@/store/user';
import logout from '@/assets/images/logout.svg'

// 导入SVG文件
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

// 图标路径映射表
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
  'pricingActive': pricingActiveIcon
};

const { Sider } = Layout;

const Menu: FC<{
  mode?: 'vertical' | 'horizontal' | 'inline';
  source?: 'space' | 'manage';
}> = ({ mode = 'inline', source = 'manage' }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { t } = useTranslation();
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const { allMenus, collapsed, loadMenus, toggleSider } = useMenu()
  const [menus, setMenus] = useState<MenuItem[]>([])
  const { user, storageType } = useUser()

  useEffect(() => {
    if (user.role === 'member' && source === 'space') {
      setMenus((allMenus[source] || []).filter(menu => menu.code !== 'member'))
    } else if (user) {
      setMenus(allMenus[source] || [])
    }
  }, [source, allMenus, user])
  // 处理菜单项点击
  const handleMenuClick: MenuProps['onClick'] = (e) => {
    const path = e.key;
    if (path) {
      navigate(path);
      setSelectedKeys([path]);
    }
  };

  // 将自定义菜单格式转换为Ant Design Menu的items格式
  const generateMenuItems = (menuList: MenuItem[]): MenuProps['items'] => {

    return menuList.filter(menu => menu.display).map((menu) => {
      const iconKey = selectedKeys.includes(menu.path || '') ? `${menu.code}Active` : menu.code;
      const iconSrc = iconPathMap[iconKey as keyof typeof iconPathMap];
      const subs = (menu.subs || []).filter(sub => sub.display);
      // 叶子节点
      if (!subs || subs.length === 0) {
        if (!menu.path) return null;
        
        return {
          key: menu.path,
          title: menu.i18nKey ? t(menu.i18nKey) : menu.label,
          label: menu.i18nKey ? t(menu.i18nKey) : menu.label,
          icon: iconSrc ? <img 
            src={iconSrc} 
            className="rb:w-[16px] rb:h-[16px] rb:mr-[8px]" 
          /> : null,
        };
      }
      
      // 有子菜单的节点

      const menuLabel = menu.i18nKey ? t(menu.i18nKey) : menu.label;
      return {
        key: `submenu-${menu.id}`,
        title: menuLabel,
        label: menuLabel,
        icon: iconSrc ? <img 
          src={iconSrc} 
          className="rb:w-[16px] rb:h-[16px] rb:mr-[8px]" 
        /> : <UserOutlined/>,
        children: generateMenuItems(subs),
      };
    }).filter(Boolean);
  };
  
  // 生成菜单项
  const menuItems = generateMenuItems(menus);
  // 初始加载菜单
  useEffect(() => {
    loadMenus(source);
  }, [])

  // 处理当前路径匹配
  useEffect(() => {
    // 使用location.pathname获取当前路径，确保与路由系统保持一致
    const currentPath = location.pathname || '/';

    // 尝试找到匹配的菜单项和对应的父菜单路径
    const findMatchingKey = (menuList: MenuItem[], parentPaths: string[] = []): { key: string | null; } => {
      for (const menu of menuList) {
        if (menu.path) {
          const menuPath = menu.path[0] !== '/' ? '/' + menu.path : menu.path;
          
          // 精确匹配或路径前缀匹配（确保是完整路径段匹配）
          const isExactMatch = menuPath === currentPath;
          const isPrefixMatch = currentPath.startsWith(menuPath + '/') || 
                               currentPath === menuPath;
          
          if (isExactMatch || isPrefixMatch) {
            return { key: menu.path };
          }
        }
        
        // 递归检查子菜单
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
      <div className={clsx(styles.title, {
        [styles.collapsed]: collapsed,
        'rb:flex rb:items-center rb:text-[14px]! rb:py-[8px]!': !collapsed && source === 'space' && user.current_workspace_name,
      })}>
        {!collapsed && source === 'space' && user.current_workspace_name
          ? <div className="rb:w-[175px] rb:text-center">
            <div className="rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{user.current_workspace_name}</div>
            <span className="rb:text-[12px] rb:text-[#5B6167] rb:leading-[16px] rb:font-regular">
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
      {user?.is_superuser && source === 'space' &&
        <div
          onClick={goToSpace}
          className="rb:pl-[25px] rb:flex rb:items-center rb:justify-start rb:absolute rb:bottom-[32px] rb:w-full rb:text-[12px] rb:text-[#5B6167] rb:hover:text-[#212332] rb:leading-[16px] rb:font-regular rb:text-center rb:mt-[24px] rb:cursor-pointer"
        >
          <img src={logout} className="rb:w-[16px] rb:h-[16px] rb:mr-[16px]" />
          {collapsed ? null : t('common.returnToSpace')}
        </div>
      }
    </Sider>
  );
};

export default Menu;