/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-07 14:55:04 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-04 18:22:45
 */
import { type FC, useEffect, useState, useRef } from "react";
import { useTranslation } from 'react-i18next'
import { Form, Flex } from 'antd'

import ModelSelect from '@/components/ModelSelect'
import type { Model } from '@/views/ModelManagement/types';
import ModelConfigModal, { fieldConfigs } from './ModelConfigModal';
import type { ModelConfigModalRef, ModelConfigForm } from './type'
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin';

const ModelConfig: FC<{ parentName?: string; variableOptions: Suggestion[] }> = ({ parentName, variableOptions }) => {
  const { t } = useTranslation()
  const form = Form.useFormInstance()
  const values = Form.useWatch<ModelConfigForm>([], form)
  const model_id = Form.useWatch(['model_id'], form)
  const [options, setOptions] = useState<Model[]>([])
  const modelConfigModalRef = useRef<ModelConfigModalRef>(null)

  const updateOptions = (options: Model[]) => {
    setOptions(options)
  }

  useEffect(() => {
    if (model_id && options) {
      const model = options.find(item => item.id === model_id)
      form.setFieldValue('capability', model?.capability || [])
    } else {
      form.setFieldValue('capability', [])
    }
  }, [model_id, options])

  const handleSetConfig = () => {
    modelConfigModalRef.current?.handleOpen(parentName ? values?.[parentName] : values as ModelConfigForm)
  }
  const handleRefresh = (values?: ModelConfigForm) => {
    if (parentName) {
      form.setFieldValue(parentName, values)
    } else {
      form.setFieldsValue(values)
    }
  }

  return (
    <>
      <Form.Item
        label={t('workflow.config.llm.model_id')}
        required
      >
        <Flex align="center" gap={12}>
          <Form.Item name={parentName ? [parentName, "model_id"] : "model_id"} className="rb:flex-1! rb:mb-0!">
            <ModelSelect
              placeholder={t('common.pleaseSelect')}
              params={{ type: 'llm,chat' }}
              className="rb:w-full!"
              size="small"
              onChange={() => form.setFieldValue('json_output', false)}
              updateOptions={updateOptions}
            />
          </Form.Item>
          <div
            className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/application/set.svg')]"
            onClick={handleSetConfig}
          ></div>
        </Flex>
      </Form.Item>

      {Object.keys(fieldConfigs).map(key => (
        <Form.Item key={key} name={parentName ? [parentName, key] : key} hidden />
      ))}

      <ModelConfigModal
        ref={modelConfigModalRef}
        refresh={handleRefresh}
        variableOptions={variableOptions}
      />
    </>
  );
};

export default ModelConfig;