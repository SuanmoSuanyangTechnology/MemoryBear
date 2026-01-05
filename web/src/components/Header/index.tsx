import { type FC, useRef } from 'react';
import { Layout, Dropdown, Space, Breadcrumb } from 'antd';
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

const AppHeader: FC<{source?: 'space' | 'manage';}> = ({source = 'manage'}) => {
  const { t } = useTranslation();
  const location = useLocation();
  const settingModalRef = useRef<SettingModalRef>(null)
  const userInfoModalRef = useRef<UserInfoModalRef>(null)

  const { user, logout } = useUser();
  const { allBreadcrumbs } = useMenu();
  
  // 根据当前路由动态选择面包屑源
  const getBreadcrumbSource = () => {
    const pathname = location.pathname;
    
    // 知识库列表页面使用默认的 space 面包屑
    if (pathname === '/knowledge-base') {
      return 'space';
    }
    
    // 知识库详情相关页面使用独立的面包屑
    if (pathname.includes('/knowledge-base/') && pathname !== '/knowledge-base') {
      return 'space-detail';
    }
    
    // 其他页面使用传入的 source
    return source;
  };
  
  const breadcrumbSource = getBreadcrumbSource();
  const breadcrumbs = allBreadcrumbs[breadcrumbSource] || [];
  


  // 处理退出登录
  const handleLogout = () => {
    logout()
  };

  // 用户下拉菜单配置
  const userMenuItems: MenuProps['items'] = [
    {
      key: '1',
      label: (<>
        <div>{user.username}</div>
        <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-[8px]">{user.email}</div>
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
  const formatBreadcrumbNames = () => {
    return breadcrumbs.map((menu, index) => {
      const item: any = {
        title: menu.i18nKey ? t(menu.i18nKey) : menu.label,
      };
      
      // 如果是最后一项，不设置 path
      if (index === breadcrumbs.length - 1) {
        return item;
      }
      
      // 如果有自定义 onClick，使用 onClick 并设置 href 为 '#' 以显示手型光标
      if ((menu as any).onClick) {
        item.onClick = (e: React.MouseEvent) => {
          e.preventDefault();
          (menu as any).onClick(e);
        };
        item.href = '#';
      } else if (menu.path && menu.path !== '#') {
        // 只有当 path 不是 '#' 时才设置 path
        item.path = menu.path;
      }
      
      return item;
    });
  }
  return (
    <Header className={styles.header}>
      <Breadcrumb separator=">" items={formatBreadcrumbNames() as BreadcrumbProps['items']} />
      {/* 语言切换和主题切换按钮 */}
      <Space>
        {/* <Button
          size="small" 
          type="default"
          onClick={handleLanguageChange}
        >
          {t(`language.${language === 'en' ? 'zh' : 'en'}`)}
        </Button> */}
      
        {/* 用户信息下拉菜单 */}
        <Dropdown
          menu={{ 
            items: userMenuItems
          }}
        >
          <div className="rb:cursor-pointer">{user.username}</div>
        </Dropdown>
      </Space>
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