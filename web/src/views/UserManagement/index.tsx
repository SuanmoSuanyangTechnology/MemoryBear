import React, { useRef } from 'react';
import { Button, Space, App } from 'antd';
import { useTranslation } from 'react-i18next';
import CreateModal from './components/CreateModal';
import type { CreateModalRef, User, ResetPasswordModalRef } from './types'
import type { ColumnsType } from 'antd/es/table';
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

  // 打开新增用户弹窗
  const handleCreate = () => {
    userFormRef.current?.handleOpen();
  }
  // 重置密码
  const handleResetPassword = (user: User) => {
    resetPasswordModalRef.current?.handleOpen(user);
  };

  // 刷新列表数据
  const refreshTable = () => {
    tableRef.current?.loadData()
  }

  // 启用/停用
  const handleChangeStatus = async (record: User) => {
    modal.confirm({
      title: t(`user.${record.is_active ? 'disabled' : 'enabled'}Confirm`),
      okText: t('common.confirm'),
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

  // 表格列配置
  const columns: ColumnsType = [
    {
      title: t('user.userId'),
      dataIndex: 'id',
      key: 'id',
      fixed: 'left',
    },
    {
      title: <>{t('user.username')}<div className="rb:text-[#5B6167] rb:text-[12px] rb:font-medium">({t(`user.subUsername`)})</div></>,
      dataIndex: 'email',
      key: 'email',
    },
    {
      title: t('user.displayName'),
      dataIndex: 'username',
      key: 'username',
    },
    {
      title: t('user.role'),
      dataIndex: 'is_superuser',
      key: 'is_superuser',
      render: (isSuperuser: boolean) => isSuperuser ? t('user.superuser') : t('user.normalUser'),
    },
    {
      title: t('user.status'),
      dataIndex: 'is_active',
      key: 'is_active',
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
      render: (createdAt: string) => formatDateTime(createdAt, 'YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: t('user.lastLoginTime'),
      dataIndex: 'last_login_at',
      key: 'last_login_at',
      render: (lastLoginAt: string) => lastLoginAt ? formatDateTime(lastLoginAt, 'YYYY-MM-DD HH:mm:ss') : '-',
    },
    {
      title: t('common.operation'),
      key: 'action',
      fixed: 'right',
      render: (_, record) => (
        <Space size="large">
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
            {t(`common.${record.is_active ? 'disabled' : 'enabled'}`)}
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div className="rb:h-[calc(100vh-80px)] rb:overflow-hidden">
      <div className="rb:flex rb:justify-end rb:mb-[12px]">
        <Button type="primary" onClick={handleCreate}>
          {t('user.createUser')}
        </Button>
      </div>

      <Table
        ref={tableRef}
        apiUrl={getUserListUrl}
        apiParams={{
          include_inactive: true,
        }}
        columns={columns}
        rowKey="id"
        isScroll={true}
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