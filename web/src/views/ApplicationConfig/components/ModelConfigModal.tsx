import { forwardRef, useImperativeHandle, useState, useEffect } from 'react';
import { Form, Select } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ModelConfig, ModelConfigModalRef, Config, ChatData } from '../types'
import type { Model } from '@/views/ModelManagement/types'
import RbModal from '@/components/RbModal'
import RbSlider from '@/components/RbSlider'

const FormItem = Form.Item;

interface ModelConfigModalProps {
  modelList: Model[];
  refresh: (values: ModelConfig, type: 'model') => void;
  data: Config;
  chatList: ChatData[]
}

const configFields = [
  { key: 'temperature', max: 2, min: 0, step: 0.1, defaultValue: 0.7 },
  { key: 'max_tokens', max: 32000, min: 256, step: 1, defaultValue: 2000 },
  { key: 'top_p', max: 1, min: 0, step: 0.1, defaultValue: 1.0 },
  { key: 'frequency_penalty', max: 2.0, min: -2.0, step: 0.1, defaultValue: 0.0 },
  { key: 'presence_penalty', max: 2.0, min: -2.0, step: 0.1, defaultValue: 0.0 },
  { key: 'n', max: 10, min: 1, step: 1, defaultValue: 1 },
]

const ModelConfigModal = forwardRef<ModelConfigModalRef, ModelConfigModalProps>(({
  refresh,
  data,
  modelList
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<ModelConfig>();
  const [source, setSource] = useState<'chat' | 'model'>('model')

  const values = Form.useWatch([], form);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
  };

  const handleOpen = (source: 'chat' | 'model', model) => {
    setSource(source)
    if (source === 'model') {
      form.setFieldsValue({
        ...(data?.model_parameters || {}),
        default_model_config_id: data.default_model_config_id || ''
      })
    } else if (source === 'chat') {
      if (model) {
        form.setFieldsValue({
          ...(model?.model_parameters || {}),
          default_model_config_id: model.default_model_config_id || ''
        })
      } else {
        form.setFieldsValue({
          ...(data?.model_parameters || {}),
          default_model_config_id: undefined
        })
      }
    }
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        refresh(values, source)
        handleClose()
      })
      .catch((err) => {
        console.log('err', err)
      });
  }
  const handleChange = (value: string, option: Model) => {
    if (source === 'chat') {
      form.setFieldValue('label', option.name)
    }
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  useEffect(() => {
    form.setFieldsValue({...(data?.model_parameters || {})})
  }, [values?.default_model_config_id])
  return (
    <RbModal
      title={t('application.modelConfig')}
      open={visible}
      onCancel={handleClose}
      cancelText={t('application.resetDefault')}
      okText={t('application.apply')}
      onOk={handleSave}
    >
      <Form
        form={form}
        layout="vertical"
        className="rb:ml-[7px]!"
      >
        <FormItem
          name="default_model_config_id"
          label={t('application.currentModel')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <Select
            placeholder={t('common.pleaseSelect')}
            fieldNames={{
              label: 'name',
              value: 'id',
            }}
            options={modelList}
            onChange={handleChange}
          />
        </FormItem>
        {source === 'chat' && <FormItem name="label" hidden />}

        <div className="rb:text-[14px] rb:font-medium rb:text-[#5B6167] rb:mb-[16px]">{t('application.parameterConfig')}</div>

        {configFields.map(item => (
          <FormItem
            key={item.key}
            name={item.key}
            label={t(`application.${item.key}`)}
            extra={t(`application.${item.key}_desc`)}
          >
            <RbSlider 
              max={item.max}
              step={item.step}
              min={item.min}
            />
          </FormItem>
        ))}
      </Form>
    </RbModal>
  );
});

export default ModelConfigModal;