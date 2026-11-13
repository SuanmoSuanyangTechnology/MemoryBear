/**
 * Delist Confirmation Modal
 * Delist operation for listed applications in community market, supports delist reason
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal';
import { appPublicationDelist } from '@/api/application';
import type { DelistModalRef } from '../types';

const { TextArea } = Input;

/**
 * Delist Confirmation Modal Component
 */
const DelistModal = forwardRef<DelistModalRef, { onRefresh: () => void; onSuccess: () => void }>((props, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<{ delist_reason?: string }>();
  const [appName, setAppName] = useState('');
  const [publicationId, setPublicationId] = useState('');

  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setAppName('');
    setPublicationId('');
  };

  const handleOpen = (pub_id: string, app_name: string) => {
    setPublicationId(pub_id);
    setAppName(app_name);
    form.resetFields();
    setVisible(true);
  };

  const handleConfirm = () => {
    form.validateFields().then((values) => {
      setLoading(true);
      appPublicationDelist(publicationId, values)
        .then(() => {
          props.onRefresh();
          props.onSuccess();
          handleClose();
        })
        .finally(() => {
          setLoading(false);
        });
    });
  };

  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  return (
    <RbModal
      title={t('application.confirmDelistTitle', { app: appName })}
      open={visible}
      onCancel={handleClose}
      okText={t('application.confirmDelist')}
      cancelText={t('common.cancel')}
      okButtonProps={{ danger: true }}
      confirmLoading={loading}
      onOk={handleConfirm}
      width={480}
    >
      <div className="rb:text-[13px] rb:text-gray-600 rb:mb-4">
        {t('application.confirmDelistContent')}
      </div>
      <Form form={form} layout="vertical">
        <Form.Item
          name="delist_reason"
          label={t('application.delistReason')}
        >
          <TextArea
            rows={4}
            placeholder={t('application.delistReasonPlaceholder')}
            maxLength={500}
            showCount
          />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default DelistModal;
