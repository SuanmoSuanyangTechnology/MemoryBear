/*
 * @Author: ZhaoYing 
 * @Date: 2026-06-05 13:33:26 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-06-05 13:33:26 
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Form, Input, Radio, App } from 'antd';

import RbModal from '@/components/RbModal';
import type { MetadataModalRef, MetadataField } from '@/views/KnowledgeBase/types';
import { createMetadataField, updateMetadataField } from '@/api/knowledgeBase';

const FormItem = Form.Item;

const MetadataModal = forwardRef<MetadataModalRef, { refresh: () => void }>(({ refresh }, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();

  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm();
  const [kbId, setKbId] = useState<string>('');
  const [editItem, setEditItem] = useState<MetadataField | undefined>();

  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setKbId('');
    setEditItem(undefined);
  };

  const handleOpen = (knowledgeBaseId: string, item?: MetadataField) => {
    setKbId(knowledgeBaseId);
    setEditItem(item);
    
    if (item) {
      form.setFieldsValue({ name: item.name });
    } else {
      form.resetFields();
      form.setFieldsValue({ type: 'string' });
    }
    
    setVisible(true);
  };

  const handleSave = () => {
    form.validateFields().then((values) => {
      if (editItem) {
        updateMetadataField(kbId, editItem.id, { name: values.name })
          .then(() => {
            message.success(t('common.operateSuccess'));
            refresh?.();
            handleClose();
          })
          .catch((error) => {
            console.error('Failed to update metadata:', error);
          });
      } else {
        createMetadataField(kbId, values)
          .then(() => {
            message.success(t('common.operateSuccess'));
            refresh?.();
            handleClose();
          })
          .catch((error) => {
            console.error('Failed to create metadata:', error);
          });
      }
    });
  };

  useImperativeHandle(ref, () => ({ handleOpen }));

  const isEdit = !!editItem;

  return (
    <RbModal
      title={isEdit ? t('knowledgeBase.metadata.editTitle') : t('knowledgeBase.metadata.addTitle')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
    >
      <Form
        form={form}
        layout="vertical"
        scrollToFirstError={{ behavior: 'instant', block: 'end', focus: true }}
        initialValues={{ type: 'string' }}
      >
        {!isEdit && (
          <FormItem
            name="type"
            label={t('knowledgeBase.metadata.type')}
            rules={[{ required: true, message: t('common.pleaseSelect') }]}
          >
            <Radio.Group className="rb:w-full!">
              <Radio.Button value="string">String</Radio.Button>
              <Radio.Button value="number">Number</Radio.Button>
              <Radio.Button value="time">Time</Radio.Button>
            </Radio.Group>
          </FormItem>
        )}

        <FormItem
          name="name"
          label={t('knowledgeBase.metadata.name')}
          rules={[
            { required: true, message: t('common.pleaseEnter') },
            { pattern: /^[a-zA-Z_][a-zA-Z0-9_]*$/, message: t('knowledgeBase.metadata.invalidName') },
          ]}
        >
          <Input placeholder={t('knowledgeBase.metadata.namePlaceholder')} />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default MetadataModal;
