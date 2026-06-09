/*
 * @Author: ZhaoYing 
 * @Date: 2026-06-03 14:00:00 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-03 20:55:18
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Select, InputNumber } from 'antd';
import { useTranslation } from 'react-i18next';

import type { EnvVariableModalRef } from './types'
import type { EnvVariable } from '../../types';
import RbModal from '@/components/RbModal'

const FormItem = Form.Item;

interface EnvVariableModalProps {
  refresh: (value: EnvVariable, editIndex?: number) => void;
  variables?: EnvVariable[];
}

const EnvVariableModal = forwardRef<EnvVariableModalRef, EnvVariableModalProps>(({
  refresh,
  variables
}, ref) => {
  const { t } = useTranslation();

  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<EnvVariable>();
  const [loading, setLoading] = useState(false);
  const [editIndex, setEditIndex] = useState<number | undefined>(undefined);
  const valueType = Form.useWatch('value_type', form);

  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false);
    setEditIndex(undefined);
  };

  const handleOpen = (variable?: EnvVariable, index?: number) => {
    setVisible(true);
    if (variable) {
      form.setFieldsValue({
        ...variable,
        value: variable.value && variable.value_type === 'secret' ? '********************' : variable.value
      });
      setEditIndex(index);
    } else {
      form.resetFields();
      setEditIndex(undefined);
    }
  };

  const handleSave = () => {
    form.validateFields().then((values) => {
      refresh({ ...values }, editIndex);
      handleClose();
    });
  };

  useImperativeHandle(ref, () => ({ handleOpen }));

  return (
    <RbModal
      title={editIndex !== undefined ? t('workflow.editEnvVariable') : t('workflow.addEnvVariable')}
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
          label={t('workflow.config.parameter-extractor.type')}
          name="value_type"
          initialValue="secret"
        >
          <Select
            options={['secret', 'string', 'number'].map((type) => ({
              label: <span style={{ textTransform: 'capitalize' }}>{type}</span>,
              value: type,
            }))}
          />
        </FormItem>
        <FormItem
          name="name"
          label={t('workflow.config.parameter-extractor.name')}
          rules={[
            { required: true, message: t('common.pleaseEnter') },
            { pattern: /^[a-zA-Z_][a-zA-Z0-9_]*$/, message: t('workflow.config.parameter-extractor.invalidParamName') },
            {
              validator: (_, value) => {
                const duplicate = variables?.some((v, i) => v.name === value && i !== editIndex);
                return duplicate ? Promise.reject(t('workflow.config.duplicateName')) : Promise.resolve();
              }
            },
          ]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>

        <FormItem
          name="required"
          hidden={true}
          initialValue={true}
        />

        <FormItem
          name="value"
          label={t('workflow.env-variable.value')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          {valueType === 'number'
            ? <InputNumber placeholder={t('common.enter')} className="rb:w-full!" />
            : <Input placeholder={t('common.enter')} />
          }
        </FormItem>

        <FormItem name="description" label={t('workflow.config.parameter-extractor.desc')}>
          <Input.TextArea placeholder={t('common.enter')} />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default EnvVariableModal;