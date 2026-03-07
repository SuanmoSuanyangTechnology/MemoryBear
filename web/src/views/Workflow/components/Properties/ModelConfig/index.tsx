import { type FC } from "react";
import { useTranslation } from 'react-i18next'
import { Form } from 'antd'

import RbSlider from '@/components/RbSlider'
import RbCard from '@/components/RbCard/Card'
import ModelSelect from '@/components/ModelSelect'

const ModelConfig: FC = () => {
  const { t } = useTranslation()
  const form = Form.useFormInstance()
  const model_id = Form.useWatch(['model_id'], form)
  console.log('ModelConfig', model_id)

  return (
    <>
      <Form.Item
        name="model_id"
        label={t('workflow.config.llm.model_id')}
        className={model_id ? 'rb:mb-2!' : 'rb:mb-4!'}
      >
        <ModelSelect
          placeholder={t('common.pleaseSelect')}
          params={{ type: 'llm,chat' }}
          className="rb:w-full!"
          size="small"
        />
      </Form.Item>
      {model_id && (
        <RbCard
          title={t('workflow.config.llm.parameterSettings')}
          headerClassName="rb:min-h-8! rb:mx-2!"
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
            className="rb:mb-0!"
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
        </RbCard>
      )}
    </>
  );
};
export default ModelConfig;
