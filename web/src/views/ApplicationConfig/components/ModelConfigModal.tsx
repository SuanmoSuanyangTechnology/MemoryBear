/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:28:07 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-31 16:56:57
 */
/**
 * Model Configuration Modal
 * Allows configuring model parameters like temperature, max_tokens, top_p, etc.
 * Supports different sources: model, chat, and multi_agent
 */

import { forwardRef, useImperativeHandle, useState, useEffect } from 'react';
import { Form, type SelectProps, Checkbox } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ModelConfig, ModelConfigModalRef, Config, Source } from '../types'
import type { Model } from '@/views/ModelManagement/types'
import RbModal from '@/components/RbModal'
import RbSlider from '@/components/RbSlider'
import ModelSelect from '@/components/ModelSelect'

const FormItem = Form.Item;

/**
 * Component props
 */
interface ModelConfigModalProps {
  /** Callback to update model configuration */
  refresh: (values: ModelConfig, type: Source) => void;
  /** Application configuration data */
  data: Config;
}

/**
 * Modal for configuring model parameters
 */
/**
 * Model parameter configuration fields
 */
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
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<ModelConfig>();
  const [source, setSource] = useState<Source>('model')

  const values = Form.useWatch([], form);

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
  };

  /** Open modal with configuration source */
  const handleOpen = (source: Source, model?: any) => {
    setSource(source)
    if (source === 'model') {
      form.setFieldsValue({
        ...(data?.model_parameters || {}),
        default_model_config_id: data.default_model_config_id || '',
        capability: model?.capability || []
      })
    } else if (source === 'chat' || source === 'multi_agent') {
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
  /** Save model configuration */
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
  /** Handle model selection change */
  const handleChange: SelectProps['onChange'] = (_value, option) => {
    if (source === 'chat') {
      form.setFieldValue('label', (option as Model).name)
    }

    form.setFieldsValue({
      capability: (option as Model).capability,
      deep_thinking: false,
    })
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  useEffect(() => {
    const { deep_thinking: _, ...rest } = data?.model_parameters || {}
    form.setFieldsValue(rest)
  }, [values?.default_model_config_id])


  console.log('handleChange values', values)
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
        className="rb:ml-1.75!"
      >
        <FormItem
          name="default_model_config_id"
          label={t('application.currentModel')}
          rules={[{ required: source !== 'multi_agent', message: t('common.pleaseSelect') }]}
          hidden={source === 'multi_agent'}
        >
          {source !== 'multi_agent' &&
            <ModelSelect
              params={{type: 'llm,chat'}}
              placeholder={t('common.pleaseSelect')}
              onChange={handleChange}
            />
          }
        </FormItem>
        {['model', 'chat'].includes(source) && <>
          <FormItem name="capability" hidden />
          {(values?.deep_thinking || values?.capability?.includes('thinking')) && (
            <FormItem name="deep_thinking" valuePropName="checked">
              <Checkbox>{t('application.deep_thinking')}</Checkbox>
            </FormItem>
          )}
        </>}
        {source === 'chat' && <FormItem name="label" hidden />}


        <div className="rb:text-[14px] rb:font-medium rb:text-[#5B6167] rb:mb-4">{t('application.parameterConfig')}</div>

        {configFields.map(item => (
          <FormItem
            key={item.key}
            name={item.key}
            label={t(`application.${item.key}`)}
            extra={<>{t(`application.${item.key}_desc`)} | {t('application.range')}: [{item.min}, {item.max}]</>}
          >
            <RbSlider 
              max={item.max}
              step={item.step}
              min={item.min}
              isInput={true}
            />
          </FormItem>
        ))}
      </Form>
    </RbModal>
  );
});

export default ModelConfigModal;