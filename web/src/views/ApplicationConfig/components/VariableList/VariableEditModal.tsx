/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:26:27 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:26:27 
 */
/**
 * Variable Edit Modal
 * Allows creating and editing application input variables
 * Supports multiple variable types: text, paragraph, number, dropdown, checkbox, API variable
 */

import { forwardRef, useImperativeHandle, useState, useRef } from 'react';
import { Form, Input, Select, InputNumber, Checkbox, Tag, Divider, Button } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ApiExtensionModalRef, Variable, VariableEditModalRef } from './types'
import RbModal from '@/components/RbModal'
import SortableList from '@/components/SortableList'
import ApiExtensionModal from './ApiExtensionModal'

const FormItem = Form.Item;

/**
 * Component props
 */
interface VariableEditModalProps {
  /** Callback to update variable */
  refreshTable: (values: Variable) => void;
}

/**
 * Supported variable types
 */
const types = [
  'text', 
  'paragraph', 
  // 'dropdownOptions', 
  'number', 
  // 'checkbox', 
  // 'apiVariable'
]
/**
 * Variable type to data type mapping
 */
const variableType = {
  text: 'string',
  paragraph: 'string',
  dropdownOptions: 'string',
  number: 'number',
  checkbox: 'boolean',
  apiVariable: 'string',
}

/**
 * Modal for editing variables
 */
const VariableEditModal = forwardRef<VariableEditModalRef, VariableEditModalProps>(({
  refreshTable
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<Variable>();
  const [loading, setLoading] = useState(false)
  const [editVo, setEditVo] = useState<Variable | null>(null)
  const apiExtensionModalRef = useRef<ApiExtensionModalRef>(null)

  const values = Form.useWatch([], form);

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setEditVo(null)
  };

  /** Open modal with optional variable data */
  const handleOpen = (variable?: Variable) => {
    setVisible(true);
    if (variable) {
      setEditVo(variable || null)
      form.setFieldsValue(variable)
    } else {
      form.resetFields();
    }
  };
  /** Save variable configuration */
  const handleSave = () => {
    form.validateFields().then((values) => {
      refreshTable({
        ...(editVo || {}),
        ...values,
      })
      handleClose()
    })
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));
  /** Handle variable type change */
  const handleChangeType = (value: Variable['type']) => {
    if (value) {
      form.setFieldsValue({
        type: value,
        name: undefined,
        display_name: undefined,
        description: undefined,
        max_length: undefined,
        options: undefined,
        api_extension: undefined,
        // default_value: undefined
      })
    }
  }
  /** Add API extension */
  const addApiExtension = () => {
    apiExtensionModalRef.current?.handleOpen()
  }
  const refreshApiExtensionList = () => {}

  return (
    <>
      <RbModal
        title={t('application.editVariable')}
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
          {/* 变量类型 */}
          <FormItem
            name="type"
            label={t('application.variableType')}
            rules={[{ required: true, message: t('common.pleaseSelect') }]}
          >
            <Select
              placeholder={t('common.pleaseSelect')}
              options={types.map(key => ({
                value: key,
                label: t(`application.${key}`),
              }))}
              onChange={handleChangeType}
              labelRender={(props) => <div className="rb:flex rb:justify-between rb:items-center">{props.label} <Tag color="blue">{variableType[props.value as keyof typeof variableType]}</Tag></div>}
              optionRender={(props) => <div className="rb:flex rb:justify-between rb:items-center">{props.label} <Tag color="blue">{variableType[props.value as keyof typeof variableType]}</Tag></div>}
            />
          </FormItem>
          {/* 变量名称 */}
          <FormItem
            name="name"
            label={t('application.variableName')}
            rules={[
              { required: true, message: t('common.pleaseEnter') },
              { pattern: /^[a-zA-Z_][a-zA-Z0-9_]*$/, message: t('application.invalidVariableName') },
            ]}
          >
            <Input 
              placeholder={t('common.enter')} 
              onBlur={(e) => {
                if (!form.getFieldValue('display_name')) {
                  form.setFieldValue('display_name', e.target.value)
                }
              }}
            />
          </FormItem>
          {/* 显示名称 */}
          <FormItem
            name="display_name"
            label={t('application.displayName')}
            rules={[{ required: true, message: t('common.pleaseEnter') }]}
          >
            <Input placeholder={t('common.enter')} />
          </FormItem>
          {/* 描述 */}
          <FormItem
            name="description"
            label={t('application.description')}
          >
            <Input placeholder={t('common.enter')} />
          </FormItem>
          {/* 最大长度 */}
          {['text', 'paragraph'].includes(values?.type) && (
            <FormItem
              name="max_length"
              label={t('application.maxLength')}
            >
              <InputNumber placeholder={t('common.enter')} style={{ width: '100%' }} />
            </FormItem>
          )}
          {/* 默认值 */}
          {/* {['text', 'paragraph', 'number', 'checkbox'].includes(values?.type) && (
            <FormItem
              name="default_value"
              label={t('application.defaultValue')}
            >
              {['text'].includes(values.type) && <Input placeholder={t('common.enter')} />}
              {['paragraph'].includes(values.type) && <Input.TextArea placeholder={t('common.enter')} />}
              {['number'].includes(values.type) && <InputNumber placeholder={t('common.enter')} style={{ width: '100%' }} />}
              {['checkbox'].includes(values.type) && <Select options={[{ value: true, label: t('application.defaultChecked') }, { value: false, label: t('application.notDefaultChecked') }]} />}
            </FormItem>
          )} */}
          {/* 选项 */}
          {['dropdownOptions'].includes(values?.type) && (
            <FormItem
              name="options"
              label={t('application.options')}
            >
              <SortableList />
            </FormItem>
          )}
          {/* API 扩展 */}
          {['apiVariable'].includes(values?.type) && (
            <FormItem
              name="api_extension"
              label={t('application.apiExtension')}
            >
              <Select
                placeholder={t('common.pleaseSelect')}
                popupRender={(menu) => (
                  <>
                    {menu}
                    <Divider style={{ margin: '8px 0' }} />
                    <Button type="text" block onClick={addApiExtension}>
                      Add item
                    </Button>
                  </>
                )}
              />
            </FormItem>
          )}
          {/* 是否必填 */}
          <FormItem
            name="required"
            valuePropName="checked"
          >
            <Checkbox>{t('application.required')}</Checkbox>
          </FormItem>
          {/* 是否隐藏 */}
          {/* <FormItem
            name="hidden"
            valuePropName="checked"
          >
            <Checkbox>{t('application.hidden')}</Checkbox>
          </FormItem> */}
        </Form>
      </RbModal>

      <ApiExtensionModal
        ref={apiExtensionModalRef}
        refresh={refreshApiExtensionList}
      />
    </>
  );
});

export default VariableEditModal;