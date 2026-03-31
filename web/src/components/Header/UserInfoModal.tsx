/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:09:47 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-25 11:40:47
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
import { Button, Flex, Space } from 'antd';
import { UnlockOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';

import { useUser } from '@/store/user';
import RbModal from '@/components/RbModal'
import { formatDateTime } from '@/utils/format';
import ResetPasswordModal from '@/views/UserManagement/components/ResetPasswordModal'
import type { ResetPasswordModalRef, VerifyPasswordModalRef, ChangeEmailModalRef } from '@/views/UserManagement/types'
import VerifyPasswordModal from '@/views/UserManagement/components/VerifyPasswordModal'
import ChangeEmailModal from '@/views/UserManagement/components/ChangeEmailModal'

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
  const { user, getUserInfo } = useUser();
  const [visible, setVisible] = useState(false);
  const verifyPasswordModalRef = useRef<VerifyPasswordModalRef>(null)
  const changeEmailModalRef = useRef<ChangeEmailModalRef>(null)

  /** Close the modal */
  const handleClose = () => {
    setVisible(false);
  };

  /** Open the modal */
  const handleOpen = () => {
    setVisible(true);
  };

  /** Open password verification modal before editing email */
  const handleEditEmail = () => {
    verifyPasswordModalRef.current?.handleOpen()
  }
  
  /** Update user information after email change */
  const updateUserInfo = () => {
    localStorage.removeItem('user')
    getUserInfo()
  }

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
      <Flex vertical gap={12}>
        <div className="rb:text-[#5B6167] rb:font-medium">{t('header.basicInfo')}</div>
        {/* Username */}
        <Flex
          justify="space-between"
          className="rb:text-[#5B6167] rb:leading-5"
        >
          <span className="rb:whitespace-nowrap">{t('user.username')}</span>
          <span className="rb:text-[#212332]">{user.username}</span>
        </Flex>
        {/* Email */}
        <Flex
          justify="space-between"
          className="rb:text-[#5B6167] rb:leading-5"
        >
          <span className="rb:whitespace-nowrap">{t('user.email')}</span>
          <Space size={8} className="rb:text-[#212332]">
            {user.email}
            <div
              className="rb:size-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/editBorder.svg')] rb:hover:bg-[url('@/assets/images/editBg.svg')]"
              onClick={handleEditEmail}
            ></div>
          </Space>
        </Flex>
        {/* Role */}
        <Flex
          justify="space-between"
          className="rb:text-[#5B6167] rb:leading-5"
        >
          <span className="rb:whitespace-nowrap">{t('user.role')}</span>
          <span className="rb:text-[#212332]">{user.is_superuser ? t('user.superuser') : t('user.normalUser')}</span>
        </Flex>
        {/* Created Date */}
        <Flex
          justify="space-between"
          className="rb:text-[#5B6167] rb:leading-5"
        >
          <span className="rb:whitespace-nowrap">{t('user.createdAt')}</span>
          <span className="rb:text-[#212332]">{formatDateTime(user.created_at, 'YYYY-MM-DD HH:mm:ss')}</span>
        </Flex>
      </Flex>
      
      {/* Security Settings Section */}
      <div className="rb:text-[#5B6167] rb:font-medium rb:mt-6 rb:mb-3">{t('header.securitySettings')}</div>

      {/* Password Change Card */}
      <Flex
        align="center"
        justify="space-between"
        gap={8}
        className="rb:bg-[#F0F3F8] rb:px-3! rb:py-2.5! rb:rounded-md"
      >
        <Flex align="center" gap={12}>
          <UnlockOutlined className="rb:text-[24px]" />
          <div>
            <div className="rb:leading-5">{t('header.changePassword')}</div>
            <div className="rb:text-[#5B6167] rb:text-[12px] rb:mt-1 rb:leading-4">{t('header.changePasswordDesc')}</div>
          </div>
        </Flex>
        <Button onClick={() => resetPasswordModalRef.current?.handleOpen(user)}>{t('common.change')}</Button>
      </Flex>

      <ResetPasswordModal
        ref={resetPasswordModalRef}
        source="changePassword"
      />
      <VerifyPasswordModal
        ref={verifyPasswordModalRef}
        refresh={() => changeEmailModalRef.current?.handleOpen()}
      />
      <ChangeEmailModal
        ref={changeEmailModalRef}
        refresh={updateUserInfo}
      />
    </RbModal>
  );
});

export default UserInfoModal;