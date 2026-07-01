/*
 * @Author: ZhaoYing 
 * @Date: 2026-06-29 15:01:28 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-06-29 15:01:28 
 */
/**
 * Delete Confirm Modal
 * Confirmation modal for deleting user memory with name verification
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App } from 'antd';
import { useTranslation } from 'react-i18next';

import type { Data } from '../types'
import RbModal from '@/components/RbModal'
import { deleteEndUser } from '@/api/memory';

interface DeleteConfirmModalProps {
  refreshTable: () => void;
}

export interface DeleteConfirmModalRef {
  handleOpen: (item: Data) => void;
  handleClose: () => void;
}

const DeleteConfirmModal = forwardRef<DeleteConfirmModalRef, DeleteConfirmModalProps>(({
  refreshTable
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm();
  const [currentItem, setCurrentItem] = useState<Data | null>(null);
  const [loading, setLoading] = useState(false);

  /** Get user display name */
  const getUserName = (item: Data | null) => {
    if (!item) return '';
    return item?.end_user?.other_name && item?.end_user?.other_name !== '' 
      ? item?.end_user?.other_name 
      : item?.end_user?.id || ''
  }

  /** Custom validator for name confirmation */
  const confirmNameValidator = () => {
    const expectedName = getUserName(currentItem);
    return {
      validator: async (_: unknown, value: string) => {
        if (value !== expectedName) {
          throw new Error(t('userMemory.deleteConfirmNameError'));
        }
      },
    };
  };

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    setCurrentItem(null);
    form.resetFields();
  };

  /** Open modal */
  const handleOpen = (item: Data) => {
    setCurrentItem(item);
    form.resetFields();
    setVisible(true);
  };

  /** Confirm delete action */
  const handleConfirmDelete = async () => {
    const id = currentItem?.end_user?.id;
    if (!id) return
    form.validateFields()
      .then(() => {
        setLoading(true)
        deleteEndUser(id)
          .then(() => {
            message.success(t('common.deleteSuccess'));
            refreshTable();
            handleClose();
          })
          .finally(() => {
            setLoading(false)
          })
      })
  };

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('userMemory.deleteMemoryStore')}
      open={visible}
      onCancel={handleClose}
      confirmLoading={loading}
      okText={t('userMemory.permanentDelete')}
      okButtonProps={{ danger: true }}
      onOk={handleConfirmDelete}
    >
      <div className="rb:space-y-4">
        <p className="rb:text-[#5B6167]">
          {t('userMemory.deleteWarning', {
            name: getUserName(currentItem),
            count: currentItem?.memory_num?.total || 0
          })}
        </p>
        <Form form={form} layout="vertical">
          <Form.Item
            name="confirmName"
            label={t('userMemory.enterNameConfirm', { name: getUserName(currentItem) })}
            rules={[
              confirmNameValidator(),
            ]}
          >
            <Input 
              placeholder={t('userMemory.enterNamePlaceholder')}
              className="rb:w-full"
            />
          </Form.Item>
        </Form>
      </div>
    </RbModal>
  );
});

export default DeleteConfirmModal;