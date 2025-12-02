import { forwardRef, useImperativeHandle, useState, useRef } from 'react';
import { Button } from 'antd';
import { UnlockOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { useUser } from '@/store/user';

import RbModal from '@/components/RbModal'
import { formatDateTime } from '@/utils/format';
import ResetPasswordModal from '@/views/UserManagement/components/ResetPasswordModal'
import type { ResetPasswordModalRef } from '@/views/UserManagement/types'

export interface UserInfoModalRef {
  handleOpen: () => void;
  handleClose: () => void;
}

const UserInfoModal = forwardRef<UserInfoModalRef>((_props, ref) => {
  const { t } = useTranslation();
  const resetPasswordModalRef = useRef<ResetPasswordModalRef>(null)
  const { user } = useUser();
  const [visible, setVisible] = useState(false);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
  };

  const handleOpen = () => {
    setVisible(true);
  };

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));
  return (
    <RbModal
      title={t('header.userInfo')}
      open={visible}
      onCancel={handleClose}
      footer={null}
    >
      <div className="rb:text-[#5B6167] rb:font-medium">{t('header.basicInfo')}</div>

      <div className="rb:flex rb:justify-between rb:text-[#5B6167] rb:text-[14px] rb:leading-[20px] rb:mb-[12px] rb:mt-[12px]">
        <span className="rb:whitespace-nowrap">{t('user.username')}</span>
        <span className="rb:text-[#212332]">{user.username}</span>
      </div>
      <div className="rb:flex rb:justify-between rb:text-[#5B6167] rb:text-[14px] rb:leading-[20px] rb:mb-[12px]">
        <span className="rb:whitespace-nowrap">{t('user.email')}</span>
        <span className="rb:text-[#212332]">{user.email}</span>
      </div>
      <div className="rb:flex rb:justify-between rb:text-[#5B6167] rb:text-[14px] rb:leading-[20px] rb:mb-[12px]">
        <span className="rb:whitespace-nowrap">{t('user.role')}</span>
        <span className="rb:text-[#212332]">{user.is_superuser ? t('user.superuser') : t('user.normalUser')}</span>
      </div>
      <div className="rb:flex rb:justify-between rb:text-[#5B6167] rb:text-[14px] rb:leading-[20px] rb:mb-[12px]">
        <span className="rb:whitespace-nowrap">{t('user.createdAt')}</span>
        <span className="rb:text-[#212332]">{formatDateTime(user.created_at, 'YYYY-MM-DD HH:mm:ss')}</span>
      </div>
      <div className="rb:text-[#5B6167] rb:font-medium rb:mt-[24px]">{t('header.securitySettings')}</div>

      <div className="rb:mt-[12px] rb:bg-[#F0F3F8] rb:p-[10px_12px] rb:rounded-[6px] rb:flex rb:items-center rb:justify-between rb:gap-[8px]">
        <div className="rb:flex rb:items-center rb:gap-[12px]">
          <UnlockOutlined className="rb:text-[24px]" />
          <div>
            <div className="rb:leading-[20px]">{t('header.changePassword')}</div>
            <div className="rb:text-[#5B6167] rb:text-[12px] rb:mt-[4px] rb:leading-[16px]">{t('header.changePasswordDesc')}</div>
          </div>
        </div>
        <Button onClick={() => resetPasswordModalRef.current?.handleOpen(user)}>{t('common.change')}</Button>
      </div>

      <ResetPasswordModal
        ref={resetPasswordModalRef}
        source="changePassword"
      />
    </RbModal>
  );
});

export default UserInfoModal;