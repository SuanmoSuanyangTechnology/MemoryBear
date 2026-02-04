/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:10:39 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 14:10:39 
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App } from 'antd';
import { useTranslation } from 'react-i18next';

import type { AddClassItem, OntologyClassModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { createOntologyClass } from '@/api/ontology'

const FormItem = Form.Item;

/**
 * Props for OntologyClassModal component
 */
interface OntologyClassModalProps {
  /** Callback function to refresh parent list after save */
  refresh: () => void;
}

/**
 * Modal component for adding new ontology classes
 * Provides form interface for class name and description
 */
const OntologyClassModal = forwardRef<OntologyClassModalRef, OntologyClassModalProps>(({
  refresh
}, ref) => {
  // Hooks
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<AddClassItem>();
  
  // State
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false)
  const [scene_id, setSceneId] = useState<string | null>(null)

  /**
   * Close modal and reset form state
   */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  /**
   * Open modal for adding a new class
   * @param scene_id - Target scene identifier
   */
  const handleOpen = (scene_id: string) => {
    form.resetFields();
    setVisible(true);
    setSceneId(scene_id)
  };
  
  /**
   * Validate and submit form data to create new class
   */
  const handleSave = () => {
    if (!scene_id) return;
    form
      .validateFields()
      .then((values) => {
        setLoading(true)
        createOntologyClass({
          scene_id: scene_id,
          classes: [{ ...values }]
        }).then(() => {
            message.success(t('common.saveSuccess'));
            handleClose();
            refresh();
          })
          .finally(() => setLoading(false))
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  /**
   * Expose methods to parent component via ref
   */
  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  return (
    <RbModal
      title={t('ontology.addClass')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.create')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <FormItem
          name="class_name"
          label={t('ontology.class_name')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>

        <FormItem
          name="class_description"
          label={t('ontology.class_description')}
        >
          <Input.TextArea placeholder={t('ontology.classDescriptionPlaceholder')} />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default OntologyClassModal;