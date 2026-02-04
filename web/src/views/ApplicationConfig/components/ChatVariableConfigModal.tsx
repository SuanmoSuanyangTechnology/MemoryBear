/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:27:44 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:27:44 
 */
/**
 * Chat Variable Configuration Modal
 * Allows users to configure variable values before starting a chat session
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, InputNumber } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ChatVariableConfigModalRef } from '../types'
import type { Variable } from './VariableList/types'
import RbModal from '@/components/RbModal'

/**
 * Component props
 */
interface VariableEditModalProps {
  /** Callback to update variables */
  refresh: (values: Variable[]) => void;
}

/**
 * Modal for configuring chat variables
 */
const ChatVariableConfigModal = forwardRef<ChatVariableConfigModalRef, VariableEditModalProps>(({
  refresh,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<{variables: Variable[]}>();
  const [loading, setLoading] = useState(false)
  const [initialValues, setInitialValues] = useState<Variable[]>([])

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  /** Open modal with variable list */
  const handleOpen = (values: Variable[]) => {
    console.log('values', values)
    setVisible(true);
    form.setFieldsValue({variables: values})
    setInitialValues([...values])
  };
  /** Save variable configuration */
  const handleSave = () => {
    form.validateFields().then((values) => {
      refresh([
        ...(values?.variables ?? []),
      ])
      handleClose()
    })
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  console.log(form.getFieldValue('variables'))

  return (
    <RbModal
      title={t('application.variableConfig')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="horizontal"
        scrollToFirstError={{ behavior: 'instant', block: 'end', focus: true }}
      >
        <Form.List name="variables">
          {(fields) => (
            <>
              {fields.map(({ name }, index) => {
                const field = initialValues[index]
                return (
                  <Form.Item
                    key={name}
                    name={[name, 'value']}
                    label={`${field.name}·${field.display_name}`}
                    rules={[
                      { required: field.required, message: t('common.pleaseEnter') },
                    ]}
                  >
                    {
                      field.type === 'text' && <Input placeholder={t('common.pleaseEnter')} />
                    }
                    {
                      field.type === 'number' && <InputNumber placeholder={t('common.pleaseEnter')} className="rb:w-full!" onChange={(value) => form.setFieldValue(['variables', name, 'value'], value)} />
                    }
                    {
                      field.type === 'paragraph' && <Input.TextArea placeholder={t('common.pleaseEnter')} />
                    }
                  </Form.Item>
                )
              })}
            </>
          )}
        </Form.List>
      </Form>
    </RbModal>
  );
});

export default ChatVariableConfigModal;