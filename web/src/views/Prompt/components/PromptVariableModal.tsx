/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:43:58 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:43:58 
 */
/**
 * Prompt Variable Modal
 * Modal for adding variables to prompt with autocomplete suggestions
 */

import { forwardRef, useEffect, useImperativeHandle, useState } from 'react';
import { Form, AutoComplete, type AutoCompleteProps } from 'antd';
import { useTranslation } from 'react-i18next';

import type { PromptVariableModalRef } from '../types'
import RbModal from '@/components/RbModal'

const FormItem = Form.Item;

/**
 * Component props
 */
interface PromptVariableModalProps {
  refresh: (value: string) => void;
  variables: string[];
}

const PromptVariableModal = forwardRef<PromptVariableModalRef, PromptVariableModalProps>(({
  refresh,
  variables
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false)
  const [options, setOptions] = useState<AutoCompleteProps['options']>([])

  useEffect(() => {
    setOptions(variables.map(key => ({
      value: key,
      label: `{{${key}}}`
    })))
  }, [variables])
  /** Handle search and filter variables */
  const handleSearch = (value: string) => {
    const filterKeys = variables?.filter(key => key.includes(value))

    if (filterKeys.length) {
      setOptions(filterKeys.map(key => ({
        value: key,
        label: `{{${key}}}`
      })))
    } else {
      setOptions([{
        value: value,
        label: `{{${value}}}`
      }])
    }
  }

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  /** Open modal */
  const handleOpen = () => {
    setVisible(true);
      form.resetFields();
  };
  /** Apply variable to editor */
  const handleSave = () => {
    const variableName = form.getFieldValue('variableName')

    if (!variableName) return

    refresh(`{{${variableName}}}`)
    handleClose()
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('application.addVariable')}
      open={visible}
      onCancel={handleClose}
      confirmLoading={loading}
      onOk={handleSave}
      okText={t('application.apply')}
    >
      <Form
        form={form}
        layout="vertical"
        scrollToFirstError={{ behavior: 'instant', block: 'end', focus: true }}
      > 
        <FormItem
          name="variableName"
          label={t('application.defineVariableName')}
          extra={t('application.defineVariableNameExtra')}
        >
          <AutoComplete
            placeholder={t('application.defineVariableNamePlaceholder')}
            onSearch={handleSearch}
            options={options}
          />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default PromptVariableModal;