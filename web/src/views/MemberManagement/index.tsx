import React, { useRef } from 'react';
import { App, Button, Space } from 'antd';
import { useTranslation } from 'react-i18next';
import type { ColumnsType } from 'antd/es/table';
import type { AnyObject } from 'antd/es/_util/type';
import { deleteMember, memberListUrl } from '@/api/member';

import MemberModal from './components/MemberModal';
import type { Member, MemberModalRef } from './types'
import Tag from '@/components/Tag';
import Table, { type TableRef } from '@/components/Table'
import { formatDateTime } from '@/utils/format';

const MemberManagement: React.FC = () => {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const memberFormRef = useRef<MemberModalRef>(null);
  const tableRef = useRef<TableRef>(null);

  // 打开新增用户弹窗
  const handleEdit = (member?: Member) => {
    if (memberFormRef.current) {
      memberFormRef.current.handleOpen(member);   
    }
  }

  // 刷新列表数据
  const refreshTable = () => {
    tableRef.current?.loadData()
  }

  // 单个删除用户
  const handleDelete = async (member: Member) => {
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: member.username }),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        deleteMember(member.id)
          .then(() => {
            message.success(t('member.deleteSuccess'));
            refreshTable();
          })
      }
    })
  };

  // 表格列配置
  const columns: ColumnsType = [
    {
      title: t('member.username'),
      dataIndex: 'username',
      key: 'username',
    },
    {
      title: t('member.account'),
      dataIndex: 'account',
      key: 'account',
    },
    {
      title: t('member.role'),
      dataIndex: 'role',
      key: 'role',
      render: (role: string) => {
        return <Tag color={role === 'member' ? 'processing' : 'error'}>{t(`member.${role}`)}</Tag>
      },
    },
    {
      title: t('member.lastLoginTime'), 
      dataIndex: 'last_login_at',
      key: 'last_login_at',
      render: (last_login_at: string) => formatDateTime(last_login_at, 'YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: t('common.operation'),
      key: 'action',
      render: (_, record: AnyObject) => (
        <Space size="large">
          <Button
            type="link"
            onClick={() => handleEdit(record as Member)}
          >
            {t('common.edit')}
          </Button>
          <Button type="link" danger onClick={() => handleDelete(record as Member)}>
            {t('common.delete')}
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <>
      <div className="rb:flex rb:justify-end rb:mb-[12px]">
        <Button type="primary" onClick={() => handleEdit()}>
          {t('member.createMember')}
        </Button>
      </div>
      <Table
        ref={tableRef}
        apiUrl={memberListUrl}
        columns={columns}
        rowKey="id"
        pagination={false}
      />

      <MemberModal
        ref={memberFormRef}
        refreshTable={refreshTable}
      />
    </>
  );
};

export default MemberManagement;
