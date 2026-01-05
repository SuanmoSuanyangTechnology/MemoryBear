import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Select, Checkbox } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ParamItem, ParamEditModalRef } from './types'
import RbModal from '@/components/RbModal'

const FormItem = Form.Item;

interface ParamEditModalProps {
  refresh: (values: ParamItem, editIndex?: number) => void;
}

const types = [
  'string',
  'number', 
  'boolean',
  'array[string]',
  'array[number]',
  'array[boolean]',
  'array[object]'
]

const ParamEditModal = forwardRef<ParamEditModalRef, ParamEditModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<ParamItem>();
  const [loading, setLoading] = useState(false)
  const [editIndex, setEditIndex] = useState<number | undefined>(undefined)

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setEditIndex(undefined)
  };

  const handleOpen = (variable?: ParamItem, index?: number) => {
    setVisible(true);
    if (variable) {
      form.setFieldsValue(variable)
      setEditIndex(index)
    } else {
      form.resetFields();
      setEditIndex(undefined)
    }
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form.validateFields().then((values) => {
      refresh({ ...values }, editIndex)
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
      title={editIndex !== undefined ? t('workflow.config.parameter-extractor.editParam') : t('workflow.config.parameter-extractor.addParam')}
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
        <FormItem
          name="type"
          label={t('workflow.config.parameter-extractor.type')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <Select
            placeholder={t('common.pleaseSelect')}
            options={types.map(key => ({
              value: key,
              label: t(`workflow.config.parameter-extractor.${key}`),
            }))}
          />
        </FormItem>

        <FormItem
          name="desc"
          label={t('workflow.config.parameter-extractor.desc')}
        >
          <Input.TextArea placeholder={t('common.enter')} />
        </FormItem>

        <FormItem
          name="required"
          valuePropName="checked"
        >
          <Checkbox>{t('workflow.config.parameter-extractor.required')}</Checkbox>
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default ParamEditModal;