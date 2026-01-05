import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, InputNumber, Checkbox } from 'antd';
import { useTranslation } from 'react-i18next';

import type { StartVariableItem, VariableConfigModalRef } from '../../types'
import RbModal from '@/components/RbModal'

interface VariableEditModalProps {
  refresh: (values: StartVariableItem[]) => void;
  variables: StartVariableItem[]
}

const VariableConfigModal = forwardRef<VariableConfigModalRef, VariableEditModalProps>(({
  refresh,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<{variables: StartVariableItem[]}>();
  const [loading, setLoading] = useState(false)
  const [initialValues, setInitialValues] = useState<StartVariableItem[]>([])

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  const handleOpen = (values: StartVariableItem[]) => {
    setVisible(true);
    form.setFieldsValue({variables: values})
    setInitialValues([...values])
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form.validateFields().then((values) => {
      refresh([
        ...(values?.variables ?? []),
      ])
      handleClose()
    })
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('workflow.variableConfig')}
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
                    label={field.type === 'boolean' ? undefined : `${field.name}·${field.description}`}
                    valuePropName={field.type === 'boolean' ? 'checked' : 'value'}
                    rules={[
                      { required: field.required, message: field.type === 'boolean' ? t('common.pleaseSelect') : t('common.pleaseEnter') },
                    ]}
                  >
                    {
                      field.type === 'string' && <Input placeholder={t('common.pleaseEnter')} />
                    }
                    {
                      field.type === 'number' && <InputNumber placeholder={t('common.pleaseEnter')} style={{ width: '100%' }} />
                    }
                    {
                      field.type === 'boolean' && <Checkbox>{`${field.name}·${field.description}`}</Checkbox>
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

export default VariableConfigModal;