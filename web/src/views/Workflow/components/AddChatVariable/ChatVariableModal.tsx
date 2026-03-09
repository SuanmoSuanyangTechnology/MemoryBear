/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-30 13:59:36 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-28 16:19:26
 */
/**
 * ChatVariableModal Component
 * 
 * This component provides a modal for adding or editing chat variables in workflows.
 * It supports various variable types and provides appropriate input fields based on the selected type.
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Select, InputNumber } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ChatVariableModalRef } from './types'
import type { ChatVariable } from '../../types';
import RbModal from '@/components/RbModal'

const FormItem = Form.Item;

/**
 * Props for ChatVariableModal component
 */
interface ChatVariableModalProps {
  /**
   * Callback function to refresh variable list
   * @param {ChatVariable} value - The variable data
   * @param {number} [editIndex] - Optional index for editing existing variable
   */
  refresh: (value: ChatVariable, editIndex?: number) => void;
}

/**
 * Supported variable types
 */
const types = [
  'string',          // String type
  'number',          // Number type
  'boolean',         // Boolean type
  'object',          // Object type
  'array[string]',   // Array of strings
  'array[number]',   // Array of numbers
  'array[boolean]',  // Array of booleans
  'array[object]',   // Array of objects
]

/**
 * ChatVariableModal component
 */
const ChatVariableModal = forwardRef<ChatVariableModalRef, ChatVariableModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();

  // State management
  const [visible, setVisible] = useState(false);           // Modal visibility
  const [form] = Form.useForm<ChatVariable>();            // Form instance
  const [loading, setLoading] = useState(false);           // Loading state
  const [editIndex, setEditIndex] = useState<number | undefined>(undefined); // Index of variable being edited
  const type = Form.useWatch('type', form);                // Current selected type

  /**
   * Handle modal close
   */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false);
    setEditIndex(undefined);
  };

  /**
   * Handle modal open
   */
  const handleOpen = (variable?: ChatVariable, index?: number) => {
    setVisible(true);
    if (variable) {
      // Exclude 'default' property and set form values
      const { default: _, ...rest } = variable;
      form.setFieldsValue({ ...rest });
      setEditIndex(index);
    } else {
      // Reset form for new variable
      form.resetFields();
      setEditIndex(undefined);
    }
  };

  /**
   * Handle save/submit action
   */
  const handleSave = () => {
    form.validateFields().then((values) => {
      // Create variable with 'default' property mapped from 'defaultValue'
      refresh({ ...values, default: values.defaultValue }, editIndex);
      handleClose();
    });
  };

  // Expose handleOpen method to parent component via ref
  useImperativeHandle(ref, () => ({
    handleOpen
  }));

  return (
    <RbModal
      title={editIndex !== undefined ? t('workflow.editChatVariable') : t('workflow.addChatVariable')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
        scrollToFirstError={{ behavior: 'instant', block: 'end', focus: true }}
      >
        {/* Variable name field */}
        <FormItem
          name="name"
          label={t('workflow.config.parameter-extractor.name')}
          rules={[
            { required: true, message: t('common.pleaseEnter') },
            { pattern: /^[a-zA-Z_][a-zA-Z0-9_]*$/, message: t('workflow.config.parameter-extractor.invalidParamName') },
          ]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>
        
        {/* Variable type field */}
        <FormItem
          name="type"
          label={t('workflow.config.parameter-extractor.type')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <Select
            placeholder={t('common.pleaseSelect')}
            onChange={() => form.setFieldValue('defaultValue', undefined)}
            options={types.map(key => ({
              value: key,
              label: t(`workflow.config.parameter-extractor.${key}`),
            }))}
          />
        </FormItem>
        
        {/* Default value field - dynamic based on type */}
        <Form.Item
          name="defaultValue"
          label={t('workflow.config.parameter-extractor.default')}
        >
          {type === 'number'
            ? <InputNumber 
              placeholder={t('common.enter')} 
              style={{ width: '100%' }}
              onChange={(value) => form.setFieldValue('defaultValue', value)}
            />
            : type === 'boolean'
            ? <Select
              placeholder={t('common.pleaseSelect')}
              options={[
                { value: true, label: 'true' },
                { value: false, label: 'false' }
              ]}
            />
            : <Input placeholder={t('common.enter')} />
          }
        </Form.Item>
        
        {/* Variable description field */}
        <FormItem
          name="description"
          label={t('workflow.config.parameter-extractor.desc')}
        >
          <Input.TextArea placeholder={t('common.enter')} />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default ChatVariableModal;