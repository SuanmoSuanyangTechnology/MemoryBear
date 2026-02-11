/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:09:47 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-02 15:51:54
 */
/**
 * UserInfoModal Component
 * 
 * A modal dialog that displays user profile information and security settings.
 * Includes basic user details and password change functionality.
 * Uses forwardRef to expose open/close methods to parent components.
 * 
 * @component
 */

import { forwardRef, useImperativeHandle, useState, useRef } from 'react';
import { Button } from 'antd';
import { UnlockOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';

import { useUser } from '@/store/user';
import RbModal from '@/components/RbModal'
import { formatDateTime } from '@/utils/format';
import ResetPasswordModal from '@/views/UserManagement/components/ResetPasswordModal'
import type { ResetPasswordModalRef } from '@/views/UserManagement/types'

/** Interface for UserInfoModal ref methods exposed to parent components */
export interface UserInfoModalRef {
  /** Open the user info modal */
  handleOpen: () => void;
  /** Close the user info modal */
  handleClose: () => void;
}

/** User information modal component displaying user details and security settings */
const UserInfoModal = forwardRef<UserInfoModalRef>((_props, ref) => {
  const { t } = useTranslation();
  const resetPasswordModalRef = useRef<ResetPasswordModalRef>(null)
  const { user } = useUser();
  const [visible, setVisible] = useState(false);

  /** Close the modal */
  const handleClose = () => {
    setVisible(false);
  };

  /** Open the modal */
  const handleOpen = () => {
    setVisible(true);
  };

  /** Expose handleOpen and handleClose methods to parent component via ref */
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
      {/* Basic Information Section */}
      <div className="rb:text-[#5B6167] rb:font-medium">{t('header.basicInfo')}</div>

      {/* Username */}
      <div className="rb:flex rb:justify-between rb:text-[#5B6167] rb:text-[14px] rb:leading-5 rb:mb-3 rb:mt-3">
        <span className="rb:whitespace-nowrap">{t('user.username')}</span>
        <span className="rb:text-[#212332]">{user.username}</span>
      </div>
      {/* Email */}
      <div className="rb:flex rb:justify-between rb:text-[#5B6167] rb:text-[14px] rb:leading-5 rb:mb-3">
        <span className="rb:whitespace-nowrap">{t('user.email')}</span>
        <span className="rb:text-[#212332]">{user.email}</span>
      </div>
      {/* Role */}
      <div className="rb:flex rb:justify-between rb:text-[#5B6167] rb:text-[14px] rb:leading-5 rb:mb-3">
        <span className="rb:whitespace-nowrap">{t('user.role')}</span>
        <span className="rb:text-[#212332]">{user.is_superuser ? t('user.superuser') : t('user.normalUser')}</span>
      </div>
      {/* Created Date */}
      <div className="rb:flex rb:justify-between rb:text-[#5B6167] rb:text-[14px] rb:leading-5 rb:mb-3">
        <span className="rb:whitespace-nowrap">{t('user.createdAt')}</span>
        <span className="rb:text-[#212332]">{formatDateTime(user.created_at, 'YYYY-MM-DD HH:mm:ss')}</span>
      </div>
      
      {/* Security Settings Section */}
      <div className="rb:text-[#5B6167] rb:font-medium rb:mt-6">{t('header.securitySettings')}</div>

      {/* Password Change Card */}
       <div className="rb:mt-3 rb:bg-[#F0F3F8] rb:p-[10px_12px] rb:rounded-md rb:flex rb:items-center rb:justify-between rb:gap-2">
        <div className="rb:flex rb:items-center rb:gap-3">
          <UnlockOutlined className="rb:text-[24px]" />
          <div>
            <div className="rb:leading-5">{t('header.changePassword')}</div>
            <div className="rb:text-[#5B6167] rb:text-[12px] rb:mt-1 rb:leading-4">{t('header.changePasswordDesc')}</div>
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