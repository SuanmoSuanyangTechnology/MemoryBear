import { forwardRef, useEffect, useImperativeHandle, useState } from 'react';
import { Form, Input, App, Select, AutoComplete, type AutoCompleteProps } from 'antd';
import { useTranslation } from 'react-i18next';

import type { Application } from '@/views/ApplicationManagement/types'
import type { AiPromptVariableModalRef } from '../types'
import { createApiKey  } from '@/api/apiKey';
import RbModal from '@/components/RbModal'

const FormItem = Form.Item;

interface AiPromptVariableModalProps {
  refresh: (value: string) => void;
  variables: string[];
}

const AiPromptVariableModal = forwardRef<AiPromptVariableModalRef, AiPromptVariableModalProps>(({
  refresh,
  variables
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
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

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  const handleOpen = () => {
    setVisible(true);
      form.resetFields();
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    const variableName = form.getFieldValue('variableName')

    if (!variableName) return

    refresh(`{{${variableName}}}`)
    handleClose()
  }

  // 暴露给父组件的方法
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