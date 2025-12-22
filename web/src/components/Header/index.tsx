import { type FC, useCallback, useRef } from 'react';
import { Layout, Dropdown, Space, Breadcrumb } from 'antd';
import type { MenuProps, BreadcrumbProps } from 'antd';
import { UserOutlined, LogoutOutlined, SettingOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { useParams, useNavigate } from 'react-router-dom';
import { useUser } from '@/store/user';
import { useMenu } from '@/store/menu';
import styles from './index.module.css'
import SettingModal, { type SettingModalRef } from './SettingModal'
import UserInfoModal, { type UserInfoModalRef } from './UserInfoModal'
const { Header } = Layout;

const AppHeader: FC<{source?: 'space' | 'manage';}> = ({source = 'manage'}) => {
  const { t } = useTranslation();
  const params = useParams();
  const navigate = useNavigate();
  const settingModalRef = useRef<SettingModalRef>(null)
  const userInfoModalRef = useRef<UserInfoModalRef>(null)

  const { user, logout } = useUser();
  const { allBreadcrumbs } = useMenu();
  const breadcrumbs = allBreadcrumbs[source] || [];

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
  const formatBreadcrumbNames = useCallback(() => {
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
        // 对于三级面包屑的二级菜单，如果路径包含动态参数，替换为当前参数值
        if (breadcrumbs.length === 3 && index === 1 && menu.path.includes(':id') && params.id) {
          const dynamicPath = menu.path.replace(':id', params.id);
          item.onClick = (e: React.MouseEvent) => {
            e.preventDefault();
            navigate(dynamicPath);
          };
          item.href = '#';
        } else {
          // 只有当 path 不是 '#' 时才设置 path
          item.path = menu.path;
        }
      }
      
      return item;
    });
  }, [breadcrumbs, params.id, t, navigate])
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