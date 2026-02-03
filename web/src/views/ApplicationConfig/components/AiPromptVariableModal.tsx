/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:27:14 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:27:14 
 */
/**
 * AI Prompt Variable Modal
 * Allows users to insert variables into AI-generated prompts
 * Supports autocomplete with existing variables
 */

import { forwardRef, useEffect, useImperativeHandle, useState } from 'react';
import { Form, AutoComplete, type AutoCompleteProps } from 'antd';
import { useTranslation } from 'react-i18next';

import type { AiPromptVariableModalRef } from '../types'
import RbModal from '@/components/RbModal'

const FormItem = Form.Item;

/**
 * Component props
 */
interface AiPromptVariableModalProps {
  /** Callback to insert variable into prompt */
  refresh: (value: string) => void;
  /** List of available variables */
  variables: string[];
}

/**
 * Variable selection modal for AI prompt assistant
 */
const AiPromptVariableModal = forwardRef<AiPromptVariableModalRef, AiPromptVariableModalProps>(({
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
  /** Search and filter variables */
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
  /** Apply selected variable */
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

export default AiPromptVariableModal;