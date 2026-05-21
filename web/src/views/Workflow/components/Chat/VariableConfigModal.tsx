import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, InputNumber, Checkbox, Select } from 'antd';
import { useTranslation } from 'react-i18next';

import type { VariableConfigModalRef } from '../../types'
import type { Variable } from '../Properties/VariableList/types'
import RbModal from '@/components/RbModal'
import FileVarInput from '../SingleNodeRun/FileVarInput'
import CodeMirrorEditor from '@/components/CodeMirrorEditor';

interface VariableEditModalProps {
  refresh: (values: Variable[]) => void;
}

const VariableConfigModal = forwardRef<VariableConfigModalRef, VariableEditModalProps>(({
  refresh,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<{variables: Variable[]}>();
  const [loading, setLoading] = useState(false)
  const [initialValues, setInitialValues] = useState<Variable[]>([])

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  const handleOpen = (values: Variable[]) => {
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
                if (field.type.includes('file')) {
                  return (
                    <FileVarInput
                      name={[name, 'value'] as string[]}
                      dataType={field.type}
                      form={form}
                      defaultValue={field.defaultValue || []}
                    />
                  )
                }
                return (
                  <Form.Item
                    key={name}
                    name={[name, 'value']}
                    label={field.type === 'boolean' ? undefined : `${field.name}·${field.display_name || field.description}`}
                    valuePropName={field.type === 'boolean' ? 'checked' : 'value'}
                    rules={[
                      { required: field.required, message: field.type === 'boolean' ? t('common.pleaseSelect') : t('common.pleaseEnter') },
                    ]}
                    layout={field.type.includes('file') || field.type === 'object' ? "vertical" : "horizontal"}
                  >
                    {field.type === 'object'
                      ? <CodeMirrorEditor
                          language="json"
                          variant="outlined"
                        />
                      :field.ui_type === 'select' && Array.isArray(field.options)
                      ? <Select
                        placeholder={t('common.pleaseSelect')}
                        options={field.options.map(item => ({ label: item, value: item }))}
                        popupMatchSelectWidth={false}
                      />
                      : (field.type === 'string' || field.type === 'text')
                      ? <Input placeholder={t('common.pleaseEnter')} />
                      : (field.ui_type === 'paragraph' || field.type === 'paragraph')
                      ? <Input.TextArea placeholder={t('common.pleaseEnter')} />
                      : field.type === 'number'
                      ? <InputNumber
                        placeholder={t('common.pleaseEnter')}
                        style={{ width: '100%' }}
                        onChange={(value) => form.setFieldValue(['variables', name, 'value'], value)}
                      />
                      : field.type === 'boolean'
                      ? <Checkbox>{`${field.name}·${field.display_name || field.description}`}</Checkbox>
                      : null
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