/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:42:12 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-30 11:38:42
 */
/**
 * Member Management Page
 * Manages workspace members with invite, edit, and delete functionality
 */

import React, { useRef } from 'react';
import { Button, Space } from 'antd';
import { useTranslation } from 'react-i18next';
import type { ColumnsType } from 'antd/es/table';

import { deleteMember, memberListUrl } from '@/api/member';
import MemberModal from './components/MemberModal';
import type { Member, MemberModalRef } from './types'
import Tag from '@/components/Tag';
import Table, { type TableRef } from '@/components/Table'
import { formatDateTime } from '@/utils/format';
import TablePageLayout from '@/components/TablePageLayout';
import useDeleteConfirm from '@/hooks/useDeleteConfirm';

/**
 * Member management main component
 */
const MemberManagement: React.FC = () => {
  const { t } = useTranslation();
  const deleteConfirm = useDeleteConfirm();
  const memberFormRef = useRef<MemberModalRef>(null);
  const tableRef = useRef<TableRef>(null);

  /** Open member modal for create or edit */
  const handleEdit = (member?: Member) => {
    if (memberFormRef.current) {
      memberFormRef.current.handleOpen(member);   
    }
  }

  /** Refresh member list */
  const refreshTable = () => {
    tableRef.current?.loadData()
  }

  /** Delete member with confirmation */
  const handleDelete = (member: Member) => {
    deleteConfirm({
      name: member.username,
      onOk: () => deleteMember(member.id).then(refreshTable),
    });
  };

  /** Table column configuration */
  const columns: ColumnsType<Member> = [
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
      render: (_, record) => (
        <Space size="large">
          <Button
            type="link"
            onClick={() => handleEdit(record)}
          >
            {t('common.edit')}
          </Button>
          <Button type="link" danger onClick={() => handleDelete(record)}>
            {t('common.delete')}
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <TablePageLayout
      title={t('member.memberList')}
      extra={<Button type="primary" onClick={() => handleEdit()}>+ {t('member.createMember')}</Button>}
    >
      <Table<Member>
        ref={tableRef}
        apiUrl={memberListUrl}
        columns={columns}
        rowKey="id"
        pagination={false}
        scrollY="calc(100vh - 248px)"
      />

      <MemberModal
        ref={memberFormRef}
        refreshTable={refreshTable}
      />
    </TablePageLayout>
  );
};

export default MemberManagement;
