/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:10:28 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 14:10:28 
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App } from 'antd';
import { useTranslation } from 'react-i18next';

import type { OntologyItem, OntologyModalData, OntologyModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { createOntologyScene, updateOntologyScene } from '@/api/ontology'

const FormItem = Form.Item;

/**
 * Props for OntologyModal component
 */
interface OntologyModalProps {
  /** Callback function to refresh parent list after save */
  refresh: () => void;
}

/**
 * Modal component for creating or editing ontology scenes
 * Provides form interface for scene name and description
 */
const OntologyModal = forwardRef<OntologyModalRef, OntologyModalProps>(({
  refresh
}, ref) => {
  // Hooks
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<OntologyModalData>();
  
  // State
  const [visible, setVisible] = useState(false);
  const [editVo, setEditVo] = useState<OntologyItem | null>(null)
  const [loading, setLoading] = useState(false)

  /**
   * Close modal and reset form state
   */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setEditVo(null)
  };

  /**
   * Open modal for creating or editing
   * @param vo - Optional ontology item data for edit mode
   */
  const handleOpen = (vo?: OntologyItem) => {
    if (vo) {
      setEditVo(vo);
      form.setFieldsValue(vo);
    } else {
      form.resetFields();
    }
    setVisible(true);
  };
  
  /**
   * Validate and submit form data
   * Creates new scene or updates existing one based on editVo
   */
  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        setLoading(true)
        const request = editVo?.scene_id ? updateOntologyScene(editVo.scene_id, values) : createOntologyScene(values)
        request
          .then(() => {
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
      title={editVo?.scene_id ? t('ontology.edit') : t('ontology.create')}
      open={visible}
      onCancel={handleClose}
      okText={editVo?.scene_id ? t('common.save') : t('common.create')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <FormItem
          name="scene_name"
          label={t('ontology.scene_name')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>

        <FormItem
          name="scene_description"
          label={t('ontology.scene_description')}
        >
          <Input.TextArea placeholder={t('ontology.descriptionPlaceholder')} />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default OntologyModal;