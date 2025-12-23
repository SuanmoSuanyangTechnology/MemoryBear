import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Select, InputNumber, Checkbox, Tag } from 'antd';
import { useTranslation } from 'react-i18next';

import type { StartVariableItem, VariableEditModalRef } from '../../types'
import RbModal from '@/components/RbModal'
import SortableList from '@/components/SortableList'

const FormItem = Form.Item;

interface VariableEditModalProps {
  refresh: (values: StartVariableItem) => void;
}

const types = [
  'string',
  'number', 
  'boolean',
  // 'array',
  // 'object'
]
const variableType = {
  string: 'string',
  number: 'number',
  boolean: 'boolean',
  // array: 'array',
  // object: 'object',
}
const initialValues = {
  max_length: 48,
  required: true
}

const VariableEditModal = forwardRef<VariableEditModalRef, VariableEditModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<StartVariableItem>();
  const [loading, setLoading] = useState(false)
  const [editVo, setEditVo] = useState<StartVariableItem | null>(null)

  const values = Form.useWatch([], form);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setEditVo(null)
  };

  const handleOpen = (variable?: StartVariableItem) => {
    setVisible(true);
    if (variable) {
      setEditVo(variable || null)
      form.setFieldsValue(variable)
    } else {
      form.resetFields();
    }
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form.validateFields().then((values) => {
      refresh({
        ...(editVo || {}),
        ...values,
      })
      handleClose()
    })
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));
  const nameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (values.description && values.description !== '') return
    const { value } = e.target
    form.setFieldsValue({
      description: value,
    })
  }

  return (
    <RbModal
      title={editVo ? t('workflow.config.start.editVariable') : t('workflow.config.start.addVariable')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={initialValues}
        scrollToFirstError={{ behavior: 'instant', block: 'end', focus: true }}
      >
        {/* 变量类型 */}
        <FormItem
          name="type"
          label={t('workflow.config.start.variableType')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <Select
            placeholder={t('common.pleaseSelect')}
            options={types.map(key => ({
              value: key,
              label: t(`workflow.config.start.${key}`),
            }))}
            labelRender={(props) => <div className="rb:flex rb:justify-between rb:items-center">{props.label} <Tag color="blue">{variableType[props.value as keyof typeof variableType]}</Tag></div>}
            optionRender={(props) => <div className="rb:flex rb:justify-between rb:items-center">{props.label} <Tag color="blue">{variableType[props.value as keyof typeof variableType]}</Tag></div>}
          />
        </FormItem>
        {/* 变量名称 */}
        <FormItem
          name="name"
          label={t('workflow.config.start.variableName')}
          rules={[
            { required: true, message: t('common.pleaseEnter') },
            { pattern: /^[a-zA-Z_][a-zA-Z0-9_]*$/, message: t('workflow.config.start.invalidVariableName') },
          ]}
        >
          <Input placeholder={t('common.enter')} onBlur={nameChange} />
        </FormItem>

        {/* 显示名称 */}
        <FormItem
          name="description"
          label={t('workflow.config.start.description')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>

        {/* 最大长度 */}
        {['string'].includes(values?.type) && (
          <FormItem
            name="max_length"
            label={t('workflow.config.start.max_length')}
          >
            <InputNumber placeholder={t('common.enter')} style={{ width: '100%' }} />
          </FormItem>
        )}
        {/* 默认值 */}
        {['string', 'number', 'boolean'].includes(values?.type) && (
          <FormItem
            name="default"
            label={t('workflow.config.start.default')}
          >
            {['string'].includes(values.type) && <Input placeholder={t('common.enter')} />}
            {['number'].includes(values.type) && <InputNumber placeholder={t('common.enter')} style={{ width: '100%' }} />}
            {['boolean'].includes(values.type) && <Select placeholder={t('common.pleaseSelect')} options={[{ value: true, label: t('workflow.config.start.defaultChecked') }, { value: false, label: t('workflow.config.start.notDefaultChecked') }]} />}
          </FormItem>
        )}
        {/* 选项 */}
        {['array'].includes(values?.type) && (
          <FormItem
            name="options"
            label={t('workflow.config.start.options')}
          >
            <SortableList />
          </FormItem>
        )}
        {/* 是否必填 */}
        <FormItem
          name="required"
          valuePropName="checked"
        >
          <Checkbox>{t('workflow.config.start.required')}</Checkbox>
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default VariableEditModal;