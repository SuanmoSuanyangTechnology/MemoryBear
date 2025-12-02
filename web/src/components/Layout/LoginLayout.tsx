import { Outlet } from 'react-router-dom';
import { type FC } from 'react';

// 基础布局组件，用于展示内容并保留用户信息获取功能
const LoginLayout: FC = () => {

  return (
    <div className="rb:relative rb:h-full rb:w-full">
      <Outlet />
    </div>
  )
};

export default LoginLayout;