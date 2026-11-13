/**
 * Relist Modal
 * Relist operation for delisted applications in community market, select approved version to relist directly
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal';
import CustomSelect from '@/components/CustomSelect';
import { appPublicationRelist, resourcesVersionListUrl } from '@/api/application';
import type { RelistModalRef, MySharedOutItem } from '../types';

const { TextArea } = Input;

/**
 * Relist Modal Component
 */
const RelistModal = forwardRef<RelistModalRef, { onRefresh: () => void; onSuccess: () => void }>((props, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<{ version: string; relist_note?: string }>();
  const [appName, setAppName] = useState('');
  const [marketResourceId, setMarketResourceId] = useState('');

  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setAppName('');
    setMarketResourceId('');
  };

  const handleOpen = (item: MySharedOutItem) => {
    if (!item.market_resource_id) return
    setMarketResourceId(item.market_resource_id as string);
    setAppName(item.app_name);
    form.setFieldValue('version', item.version)
    setVisible(true);
  };

  const handleConfirm = () => {
    form.validateFields().then((values) => {
      setLoading(true);
      appPublicationRelist(marketResourceId, {
        version: values.version,
        relist_note: values.relist_note || undefined,
      })
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
      title={t('application.confirmRelistTitle', { app: appName })}
      open={visible}
      onCancel={handleClose}
      okText={t('application.confirmRelist')}
      cancelText={t('common.cancel')}
      confirmLoading={loading}
      onOk={handleConfirm}
      width={480}
    >
      <div className="rb:text-[13px] rb:text-gray-600 rb:mb-4">
        {t('application.confirmRelistContent')}
      </div>
      <Form form={form} layout="vertical">
        <Form.Item
          name="version"
          label={t('application.relistVersion')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <CustomSelect
            url={resourcesVersionListUrl(marketResourceId)}
            valueKey="version"
            labelKey="label"
            hasAll={false}
            placeholder={t('common.pleaseSelect')}
            format={(items) =>
              items.map((item) => ({
                ...item,
                label: (
                  <>
                    {item.version} · {t('application.relistVersionApproved')} · {item.change_note || ''}
                  </>
                ),
              }))
            }
          />
        </Form.Item>
        <Form.Item
          name="relist_note"
          label={t('application.relistReason')}
        >
          <TextArea
            rows={4}
            placeholder={t('application.relistReasonPlaceholder')}
            maxLength={500}
            showCount
          />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default RelistModal;
