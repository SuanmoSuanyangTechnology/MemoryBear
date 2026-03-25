/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:51:08 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-25 16:45:18
 */
/**
 * User Management Page
 * Manages users with create, enable/disable, and password reset capabilities
 */

import React, { useRef } from 'react';
import { Button, App, Flex } from 'antd';
import { useTranslation } from 'react-i18next';
import type { ColumnsType } from 'antd/es/table';

import CreateModal from './components/CreateModal';
import type { CreateModalRef, User, ResetPasswordModalRef } from './types'
import Table, { type TableRef } from '@/components/Table'
import StatusTag from '@/components/StatusTag'
import { deleteUser, enableUser, getUserListUrl } from '@/api/user'
import ResetPasswordModal from './components/ResetPasswordModal'
import { formatDateTime } from '@/utils/format';

const UserManagement: React.FC = () => {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();

  const userFormRef = useRef<CreateModalRef>(null);
  const resetPasswordModalRef = useRef<ResetPasswordModalRef>(null);
  const tableRef = useRef<TableRef>(null);

  /** Open create user modal */
  const handleCreate = () => {
    userFormRef.current?.handleOpen();
  }
  /** Reset user password */
  const handleResetPassword = (user: User) => {
    resetPasswordModalRef.current?.handleOpen(user);
  };

  /** Refresh table data */
  const refreshTable = () => {
    tableRef.current?.loadData()
  }

  /** Enable/disable user */
  const handleChangeStatus = async (record: User) => {
    modal.confirm({
      title: t(`user.${record.is_active ? 'disabled' : 'enabled'}Confirm`),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        const res = record.is_active ? deleteUser(record.id) : enableUser(record.id);

        res.then(() => {
          message.success(t(`user.${record.is_active ? 'disabled' : 'enabled'}ConfirmSuccess`));
          refreshTable();
        })
      },
    })
  };

  /** Table column configuration */
  const columns: ColumnsType<User> = [
    {
      title: t('user.userId'),
      dataIndex: 'id',
      key: 'id',
      width: 190,
      className: 'rb:text-[#212332]'
    },
    {
      title: <>{t('user.username')}<div>({t(`user.subUsername`)})</div></>,
      dataIndex: 'email',
      key: 'email',
      width: 210,
    },
    {
      title: t('user.displayName'),
      dataIndex: 'username',
      key: 'username',
      width: 130,
    },
    {
      title: t('user.role'),
      dataIndex: 'is_superuser',
      key: 'is_superuser',
      width: 70,
      render: (isSuperuser: boolean) => isSuperuser ? t('user.superuser') : t('user.normalUser'),
    },
    {
      title: t('user.status'),
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (isActive: boolean) => (
        <StatusTag 
          text={isActive ? t('user.enabled') : t('user.disabled')}
          status={isActive ? 'success' : 'error'}
        />
      ),
    },
    {
      title: t('user.createTime'),
      dataIndex: 'created_at',
      key: 'created_at',
      width: 110,
      render: (createdAt: string) => formatDateTime(createdAt, 'YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: t('user.lastLoginTime'),
      dataIndex: 'last_login_at',
      key: 'last_login_at',
      width: 110,
      render: (lastLoginAt: string) => lastLoginAt ? formatDateTime(lastLoginAt, 'YYYY-MM-DD HH:mm:ss') : '-',
    },
    {
      title: t('common.operation'),
      key: 'action',
      width: 137,
      render: (_, record) => (
        <Flex vertical justify="start" align="start">
          {record.is_active &&
            <Button
              type="link"
              onClick={() => handleResetPassword(record as User)}
            >
              {t('user.resetPassword')}
            </Button>
          }
          <Button
            type="link"
            onClick={() => handleChangeStatus(record as User)}
          >
            {t(`user.${record.is_active ? 'disabledOpera' : 'enabledOpera'}`)}
          </Button>
        </Flex>
      ),
    },
  ];

  return (
    <div className="rb:h-[calc(100vh-80px)] rb:overflow-hidden rb:bg-white rb:rounded-lg rb:pt-3 rb:px-3">
      <Flex justify="space-between" align="center" className="rb:px-1! rb:mb-3!">
        <div className="rb:gont-[MiSans-Bold] rb:font-bold rb:text-[#212332] rb:leading-5">{t('user.userList')}</div>
        <Button type="primary" onClick={handleCreate}>
          + {t('user.createUser')}
        </Button>
      </Flex>

      <Table<User>
        ref={tableRef}
        apiUrl={getUserListUrl}
        apiParams={{
          include_inactive: true,
        }}
        columns={columns}
        rowKey="id"
        isScroll={true}
        scrollY="calc(100vh - 256px)"
      />

      <CreateModal
        ref={userFormRef}
        refreshTable={refreshTable}
      />
      <ResetPasswordModal
        ref={resetPasswordModalRef}
      />
    </div>
  );
};

export default UserManagement;