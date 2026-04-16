import { type FC, useEffect, useRef, useState } from "react";
import { useTranslation } from 'react-i18next'
import { Form, Switch } from 'antd'

import RbSlider from '@/components/RbSlider'
import RbCard from '@/components/RbCard/Card'
import ModelSelect, { type ModelSelectRef } from '@/components/ModelSelect'
import type { Model } from '@/views/ModelManagement/types';

const ModelConfig: FC = () => {
  const { t } = useTranslation()
  const form = Form.useFormInstance()
  const model_id = Form.useWatch(['model_id'], form)
  const modelSelectRef = useRef<ModelSelectRef>(null)
  const [selectedModel, setSelectedModel] = useState<Model | null>(null)

  useEffect(() => {
    if (model_id && modelSelectRef.current?.options) {
      const model = modelSelectRef.current?.options.find(item => item.id === model_id)
      setSelectedModel(model || null)
      form.setFieldValue('json_output', false)
    } else {
      setSelectedModel(null)
    }
  }, [model_id, modelSelectRef.current?.options])
  console.log('ModelConfig', model_id)

  return (
    <>
      <Form.Item
        name="model_id"
        label={t('workflow.config.llm.model_id')}
        className={model_id ? 'rb:mb-2!' : 'rb:mb-4!'}
        required
      >
        <ModelSelect
          ref={modelSelectRef}
          placeholder={t('common.pleaseSelect')}
          params={{ type: 'llm,chat' }}
          className="rb:w-full!"
          size="small"
        />
      </Form.Item>
      {model_id && (
        <RbCard
          title={t('workflow.config.llm.parameterSettings')}
          headerClassName="rb:min-h-8! rb:mx-2! rb:text-[12px]!"
          bodyClassName="rb:pt-[14px]! rb:px-2! rb:pb-2!"
          className="rb-border! rb:mb-4!"
          variant="outlined"
        >
          <Form.Item
            name="temperature"
            label={t('workflow.config.llm.temperature')}
            className="rb:mb-1.5!"
          >
            <RbSlider 
              min={0}
              max={2}
              step={0.1}
              isInput={true}
              size="small"
              className="rb:-mt-2!"
            />
          </Form.Item>
          <Form.Item
            name="max_tokens"
            label={t('workflow.config.llm.max_tokens')}
            className="rb:mb-1.5!"
          >
            <RbSlider 
              min={256}
              max={32000}
              step={1}
              isInput={true}
              size="small"
              className="rb:-mt-2!"
            />
          </Form.Item>
          <Form.Item
            name="json_output"
            valuePropName="checked"
            label={t('workflow.config.llm.json_output')}
            layout="horizontal"
            className="rb:mb-0!"
            hidden={!(selectedModel?.capability?.includes('json_output'))}
          >
            <Switch />
          </Form.Item>
        </RbCard>
      )}
    </>
  );
};
export default ModelConfig;
