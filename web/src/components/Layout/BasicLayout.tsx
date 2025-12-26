import { Outlet } from 'react-router-dom';
import { useEffect, type FC } from 'react';
import { useUser } from '@/store/user';

// 基础布局组件，用于展示内容并保留用户信息获取功能
const BasicLayout: FC = () => {
  const { getUserInfo, getStorageType } = useUser();
  
  // 获取用户信息
  useEffect(() => {
    getUserInfo();
    getStorageType()
  }, [getUserInfo, getStorageType]);

  return (
    <div className="rb:relative rb:h-full rb:w-full">
      <Outlet />
    </div>
  )
};

export default BasicLayout;