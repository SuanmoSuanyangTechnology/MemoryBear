/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:10:46 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 14:10:46 
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, App, Select, type SelectProps } from 'antd';
import { useTranslation } from 'react-i18next';

import type { OntologyExportModalData, OntologyExportModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { ontologyExport, getOntologyScenesUrl } from '@/api/ontology'
import CustomSelect from '@/components/CustomSelect';

const FormItem = Form.Item;

/**
 * Props for OntologyExportModal component
 */
interface OntologyExportModalProps {
  /** Callback function to refresh parent list after export */
  refresh: () => void;
}

/**
 * Modal component for exporting ontology scenes
 * Supports RDF/XML (.owl) and Turtle (.ttl) formats
 */
const OntologyExportModal = forwardRef<OntologyExportModalRef, OntologyExportModalProps>(({
  refresh
}, ref) => {
  // Hooks
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<OntologyExportModalData>();
  
  // State
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false)
  const [fileName, setFileName] = useState('')

  /**
   * Close modal and reset form state
   */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  /**
   * Open the export modal
   */
  const handleOpen = () => {
    form.resetFields();
    setVisible(true);
  };
  
  /**
   * Handle scene selection change to set export filename
   * @param _value - Selected scene ID
   * @param option - Selected option containing scene name
   */
  const handleChange: SelectProps['onChange'] = (_value, option) => {
    const name = Array.isArray(option) ? option[0]?.children : option?.children;
    setFileName(String(name || ''));
  }
  
  /**
   * Validate and submit form data to export ontology
   * Downloads file with appropriate extension based on format
   */
  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        setLoading(true)
        ontologyExport(values, `${fileName}.${values.format === 'rdfxml' ?'owl' : 'ttl'}`, () => {
          message.success(t('common.exportSuccess'));
          handleClose();
          refresh();
          setLoading(false)
        })
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
      title={t('ontology.export')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.export')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{ format: 'rdfxml' }}
      >
        <FormItem
          name="scene_id"
          label={t('ontology.scene_id')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <CustomSelect
            url={getOntologyScenesUrl}
            params={{ page: 1, pagesize: 100 }}
            valueKey="scene_id"
            labelKey="scene_name"
            hasAll={false}
            onChange={handleChange}
          />
        </FormItem>

        <FormItem
          name="format"
          label={t('ontology.format')}
        >
          <Select
            placeholder={t('common.pleaseSelect')}
            options={[
              { value: 'rdfxml', label: 'RDF/XML' },
              { value: 'turtle', label: 'Turtle' },
            ]}
          />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default OntologyExportModal;