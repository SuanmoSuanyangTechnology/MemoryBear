/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:28:07 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-02 16:37:51
 */
/**
 * Model Configuration Modal
 * Allows configuring model parameters like temperature, max_tokens, top_p, etc.
 * Supports different sources: model, chat, and multi_agent
 */

import { forwardRef, useImperativeHandle, useState, useEffect } from 'react';
import { Form, type SelectProps, Checkbox, Button } from 'antd';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';

import type { ModelConfig, ModelConfigModalRef, Config, Source } from '../types'
import type { Model } from '@/views/ModelManagement/types'
import RbModal from '@/components/RbModal'
import RbSlider from '@/components/RbSlider'
import ModelSelect from '@/components/ModelSelect'
import { resetAppModelConfig } from '@/api/application';

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
  { key: 'max_tokens', max: 32000, min: 256, step: 1, defaultValue: 8000 },
  { key: 'top_p', max: 1, min: 0, step: 0.1, defaultValue: 1.0 },
  { key: 'frequency_penalty', max: 2.0, min: -2.0, step: 0.1, defaultValue: 0.0 },
  { key: 'presence_penalty', max: 2.0, min: -2.0, step: 0.1, defaultValue: 0.0 },
  { key: 'n', max: 10, min: 1, step: 1, defaultValue: 1 },
]

const minThinkingBudgetTokens = 128;
const defaultThinkingBudgetTokens = 1000;

const omitOutputSettings = (modelParameters?: ModelConfig | null): ModelConfig => {
  const filteredParameters = { ...(modelParameters ?? {}) };
  delete filteredParameters.deep_thinking;
  delete filteredParameters.json_output;
  return filteredParameters;
};

const ModelConfigModal = forwardRef<ModelConfigModalRef, ModelConfigModalProps>(({
  refresh,
  data,
}, ref) => {
  const { t } = useTranslation();
  const { id } = useParams();
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
        default_model_config_id: data.default_model_config_id,
        input_modalities: model?.input_modalities ?? data.input_modalities ?? data.model_parameters?.input_modalities ?? ['text'],
        output_modalities: model?.output_modalities ?? data.output_modalities ?? data.model_parameters?.output_modalities ?? ['text'],
        features: model?.features ?? data.model_parameters?.features ?? []
      })
    } else if (source === 'chat' || source === 'multi_agent') {
      if (model) {
        form.setFieldsValue({
          ...(model?.model_parameters || {}),
          default_model_config_id: model.default_model_config_id
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
    const newValues: ModelConfig = {
      input_modalities: (option as Model | undefined)?.input_modalities ?? ['text'],
      output_modalities: (option as Model | undefined)?.output_modalities ?? ['text'],
      features: (option as Model | undefined)?.features ?? [],
      deep_thinking: (option as Model | undefined)?.features?.includes('thinking_only'),
      thinking_budget_tokens: defaultThinkingBudgetTokens,
      json_output: false,
    }
    if (source === 'chat') {
      newValues.label = (option as Model | undefined)?.name
    }
    form.setFieldsValue(newValues)
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  useEffect(() => {
    form.setFieldsValue(omitOutputSettings(data?.model_parameters))
  }, [data?.model_parameters])

  useEffect(() => {
    if (values?.deep_thinking && !values?.thinking_budget_tokens) {
      form.setFieldValue('thinking_budget_tokens', defaultThinkingBudgetTokens)
    }
  }, [values?.deep_thinking, values?.thinking_budget_tokens, defaultThinkingBudgetTokens])

  const handleReset = () => {
    if (!id) return
    resetAppModelConfig(id).then((res) => {
      form.setFieldsValue(omitOutputSettings((res || {}) as Config['model_parameters']))
    })
  }

  return (
    <RbModal
      title={t('application.modelConfig')}
      open={visible}
      onCancel={handleClose}
      footer={[
        <Button key="reset" onClick={handleReset}>{t('application.resetDefault')}</Button>,
        <Button key="apply" type="primary" onClick={handleSave}>{t('application.apply')}</Button>,
      ]}
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
              params={{type: 'llm'}}
              placeholder={t('common.pleaseSelect')}
              onChange={handleChange}
            />
          }
        </FormItem>
        {['model', 'chat'].includes(source) && <>
          <FormItem name="features" hidden />
          <FormItem name="input_modalities" hidden />
          <FormItem name="output_modalities" hidden />
        </>}
        <FormItem name="json_output" valuePropName="checked" hidden={!(values?.features?.includes('json_output'))}>
          <Checkbox>{t('application.json_output')}</Checkbox>
        </FormItem>
        <FormItem name="deep_thinking" valuePropName="checked" hidden={!['model', 'chat'].includes(source) || !(values?.deep_thinking || values?.features?.includes('thinking') || values?.features?.includes('thinking_only'))}>
          <Checkbox disabled={values?.features?.includes('thinking_only')}>{t('application.deep_thinking')}</Checkbox>
        </FormItem>
        <FormItem
          name="thinking_budget_tokens"
          label={t('application.thinking_budget_tokens')}
          hidden={!['model', 'chat'].includes(source) || !(values?.deep_thinking || values?.features?.includes('thinking') || values?.features?.includes('thinking_only'))}
          extra={<>{t('application.range')}: [{minThinkingBudgetTokens}, {t(`application.max_tokens`)}: {values?.max_tokens}]</>}
          dependencies={['max_tokens']}
          rules={[
            { required: values?.deep_thinking, message: t('common.pleaseEnter') },
            {
              validator: (_, value) => {
                const maxTokens = form.getFieldValue('max_tokens')
                const deep_thinking = values?.deep_thinking;
                if (deep_thinking && value !== undefined) {
                  if (value < minThinkingBudgetTokens) {
                    return Promise.reject(t('application.thinking_budget_tokens_min_error', { min: minThinkingBudgetTokens }))
                  }
                  if (maxTokens !== undefined && value > maxTokens) {
                    return Promise.reject(t('application.thinking_budget_tokens_max_error', { max: maxTokens }))
                  }
                }
                return Promise.resolve()
              }
            }
          ]}
        >
          <RbSlider
            step={1}
            min={minThinkingBudgetTokens}
            max={32000}
            isInput={true}
            disabled={!values?.deep_thinking}
          />
        </FormItem>
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