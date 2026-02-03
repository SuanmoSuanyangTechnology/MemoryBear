/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:10:32 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 14:10:32 
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App } from 'antd';
import { useTranslation } from 'react-i18next';

import type { OntologyImportModalData, OntologyImportModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { ontologyImport } from '@/api/ontology'
import UploadFiles from '@/components/Upload/UploadFiles';

const FormItem = Form.Item;

/**
 * Props for OntologyImportModal component
 */
interface OntologyImportModalProps {
  /** Callback function to refresh parent list after import */
  refresh: () => void;
}

/**
 * Modal component for importing ontology files
 * Supports OWL, TTL, RDF, XML file formats
 */
const OntologyImportModal = forwardRef<OntologyImportModalRef, OntologyImportModalProps>(({
  refresh
}, ref) => {
  // Hooks
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<OntologyImportModalData>();
  
  // State
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false)

  /**
   * Close modal and reset form state
   */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  /**
   * Open the import modal
   */
  const handleOpen = () => {
    form.resetFields();
    setVisible(true);
  };
  
  /**
   * Validate and submit form data to import ontology file
   * Creates FormData with file and scene information
   */
  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        const { scene_name, scene_description,  file } = values
        console.log('values', file);
        const formData = new FormData();
        formData.append('file', file[0]);
        formData.append('scene_name', scene_name);
        if (scene_description) {
          formData.append('scene_description', scene_description);
        }
        setLoading(true)
        ontologyImport(formData)
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
      title={t('ontology.import')}
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
          <Input.TextArea placeholder={t('common.enter')} />
        </FormItem>

        <FormItem
          name="file"
          label={t('ontology.file')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <UploadFiles
            isCanDrag={true}
            fileType={['owl', 'ttl', 'rdf', 'xml']}
            isAutoUpload={false}
          />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default OntologyImportModal;