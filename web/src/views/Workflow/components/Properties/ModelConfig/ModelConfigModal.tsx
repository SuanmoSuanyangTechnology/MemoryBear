/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:28:07 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-11 17:00:20
 */
/**
 * Model Configuration Modal
 * Allows configuring model parameters like temperature, max_tokens, top_p, etc.
 * Supports different sources: model, chat, and multi_agent
 */

import { forwardRef, useImperativeHandle, useState, useEffect } from 'react';
import { Form, Switch, Flex, type SelectProps, InputNumber, type InputNumberProps, Select, App, Tooltip } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx'

import type { ModelConfigForm, ModelConfigModalRef } from './type'
import type { Model } from '@/views/ModelManagement/types'
import RbModal from '@/components/RbModal'
import ModelSelect from '@/components/ModelSelect'
import RbSlider from '@/components/RbSlider'
import Editor from "../../Editor";
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin';

const FormItem = Form.Item;

/**
 * Component props
 */
interface ModelConfigModalProps {
  name?: string;
  /** Callback to update model configuration */
  refresh: (values?: ModelConfigForm) => void;
  variableOptions: Suggestion[];
}

export const fieldConfigs: Record<string, any> = {
  temperature: {
    type: 'slider',
    max: 1.99, 
    min: 0, 
    step: 0.1,
    defaultValue: 0.7
  },
  max_tokens: {
    type: 'slider',
    max: 32000, 
    min: 256, 
    step: 1, 
    defaultValue: 8000 
  },
  json_output: {
    type: 'switch',
    dependence: 'capability',
    defaultValue: false,
    hideTip: true
  },
  top_p: {
    enable: {
      type: 'switch',
      defaultValue: false
    },
    value: {
      type: 'slider',
      min: 0.1,
      max: 1,
      step: 0.1,
      defaultValue: 0.8
    }
  },
  top_k: {
    enable: {
      type: 'switch',
      defaultValue: false
    },
    value: {
      type: 'slider',
      min: 1,
      max: 100,
      step: 1,
      defaultValue: 50
    }
  },
  seed: {
    enable: {
      type: 'switch',
      defaultValue: false
    },
    value: {
      type: 'inputNumber',
      min: 0,
      max: 18446744073709551615,
      defaultValue: 1234
    }
  },
  repetition_penalty: {
    enable: {
      type: 'switch',
      defaultValue: false
    },
    value: {
      type: 'inputNumber',
      min: 0.1,
      max: 2,
      step: 0.1,
      defaultValue: 1.0
    }
  },
  // enable_search: {
  //   type: 'switch',
  //   defaultValue: false
  // },
  thinking: {
    enable: {
      type: 'switch',
      defaultValue: false
    },
    budget: {
      enable: {
        type: 'switch',
        defaultValue: false
      },
      value: {
        type: 'inputNumber',
        min: 128,
        defaultValue: 256
      }
    }
  },
  response_format: {
    enable: {
      type: 'switch',
      defaultValue: false
    },
    value: {
      type: 'select',
      options: [
        { label: 'text', value: 'text' },
        { label: 'json_object', value: 'json_object' },
      ],
      defaultValue: 'text',
    }
  },
  extra_headers: {
    enable: {
      type: 'switch',
      defaultValue: false
    },
    value: {
      type: 'editor',
    }
  },
  stop: {
    enable: {
      type: 'switch',
      defaultValue: false
    },
    value: {
      type: 'select',
      mode: 'tags',
      maxTagCount: 4,
      defaultValue: []
    }
  },
  presence_penalty: {
    enable: {
      type: 'switch',
      defaultValue: false,
      hideTip: true
    },
    value: {
      type: 'inputNumber',
      min: -2,
      max: 2,
      step: 0.1,
      defaultValue: 0
    }
  },
  frequency_penalty: {
    enable: {
      type: 'switch',
      defaultValue: false,
      hideTip: true
    },
    value: {
      type: 'inputNumber',
      min: -2,
      max: 2,
      step: 0.1,
      defaultValue: 0
    }
  }
}
const ModelConfigModal = forwardRef<ModelConfigModalRef, ModelConfigModalProps>(({
  refresh,
  variableOptions,
  name = 'model_id'
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<ModelConfigForm>();
  const [options, setOptions] = useState<Model[]>([])

  const values = Form.useWatch([], form);

  const updateOptions = (options: Model[]) => {
    setOptions(options)
  }
  useEffect(() => {
    if (values?.[name] && options) {
      const model = options.find(item => item.id === values[name])
      form.setFieldValue('capability', model?.capability || [])
    } else {
      form.setFieldValue('capability', [])
    }
  }, [values?.[name], options])

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
  };

  /** Open modal with configuration source */
  const handleOpen = (value?: ModelConfigForm) => {
    form.setFieldsValue({
      ...(value || {}),
    })
    setVisible(true);
  };
  /** Save model configuration */
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        const thinkingBudget = values?.thinking?.budget
        const budgetValue = Number(thinkingBudget?.value)
        if (thinkingBudget?.enable && budgetValue && values?.max_tokens && budgetValue > values.max_tokens) {
          form.setFields([{
            name: ['thinking', 'budget', 'value'] as any,
            errors: [t('application.thinking_budget_tokens_max_error', { max: values.max_tokens })]
          }])
          message.error(`${t('workflow.config.llm.thinking_budget')} ${t('application.thinking_budget_tokens_max_error', { max: values.max_tokens })}`)
          return
        }
        refresh(values)
        handleClose()
      })
      .catch((err) => {
        console.log('err', err)
      });
  }
  /** Handle model selection change */
  const handleChange: SelectProps['onChange'] = (_value, option) => {
    const model = option as Model
    const isThinkingOnly = model.capability?.includes('thinking_only')
    const newValues: ModelConfigForm = {
      capability: model?.capability || [],
      json_output: false,
      thinking: {
        enable: isThinkingOnly || values?.thinking?.enable || false,
        budget: {
          enable: isThinkingOnly || values?.thinking?.budget?.enable || false,
          value: values?.thinking?.budget?.value as number,
        }
      }
    }
    form.setFieldsValue(newValues)
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  const handleNumChange = (field: string | string[], value?: InputNumberProps['value'], min?: number) => {
    form.setFieldValue(field as any, value === undefined || value === null ? min : value)
  }

  return (
    <RbModal
      title={t('application.modelConfig')}
      open={visible}
      onCancel={handleClose}
      okText={t('application.apply')}
      onOk={handleSave}
    >
      <Form
        form={form}
        layout="vertical"
        className="rb:ml-1.75!"
        size="middle"
      >
        <FormItem
          name={name}
          label={t('workflow.config.llm.model_id')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <ModelSelect
            placeholder={t('common.pleaseSelect')}
            params={{ type: 'llm,chat' }}
            className="rb:w-full!"
            onChange={handleChange}
            updateOptions={updateOptions}
          />
        </FormItem>
        <FormItem name="capability" hidden />

        <div className="rb:font-medium rb:mb-4">{t('application.parameterConfig')}</div>

        <Flex vertical gap={16}>
          {Object.keys(fieldConfigs).map(field => {
            const firstFieldConfigs = fieldConfigs[field]
            const dependence = firstFieldConfigs.dependence as keyof ModelConfigForm
            const dependenceValue = (values as any)?.[dependence] as string[] | undefined
            const isHidden = dependence && !dependenceValue?.includes(field)
            const isThinkingOnly = values?.capability?.includes('thinking_only')
            if (isHidden) {
              return null
            }
            
            return (
              <div key={field}>
                {firstFieldConfigs.type === 'slider'
                  ? (
                    <Flex align="center" justify="space-between" wrap={false} gap={32}>
                      <Flex align="center" gap={4}>
                        {t(`workflow.config.llm.${field}`)}
                        {!firstFieldConfigs?.hideTip &&
                          <Tooltip title={t(`workflow.config.llm.${field}_tip`)}>
                            <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')] rb:shrink-0"></div>
                          </Tooltip>
                        }
                      </Flex>
                      <div className="rb:flex-1 rb:max-w-[50%] rb:overflow-hidden rb:pl-1.5!">
                        <FormItem
                          name={field}
                          noStyle
                        >
                          <RbSlider
                            {...firstFieldConfigs}
                            isInput={true}
                            inputClassName="rb:w-[100px]!"
                            className="rb:w-full!"
                            size="small"
                            onChange={(value) => {
                              handleNumChange([field], value, firstFieldConfigs.min)
                            }}
                          />
                        </FormItem>
                      </div>
                    </Flex>
                  )
                  : firstFieldConfigs.type === 'switch'
                  ? (
                    <Flex align="center" wrap={false} gap={8}>
                      <FormItem
                        name={field}
                        noStyle
                      >
                        <Switch />
                      </FormItem>
                      <Flex align="center" gap={4}>
                        {t(`workflow.config.llm.${field}`)}

                        {!firstFieldConfigs?.hideTip &&
                          <Tooltip title={t(`workflow.config.llm.${field}_tip`)}>
                            <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')] rb:shrink-0"></div>
                          </Tooltip>
                        }
                      </Flex>
                    </Flex>
                  )
                  : !firstFieldConfigs.type
                  ? (<div className={field === 'thinking' && !values?.capability?.includes('thinking_only') && !values?.capability?.includes('thinking') ? 'rb:hidden' : ''}>
                    <Flex align="center" justify="space-between" wrap={false} gap={32}>
                      <Flex align="center" wrap={false} gap={8}
                        className={clsx({
                          'rb:w-[50%]': !!firstFieldConfigs.value?.type,
                        })}
                      >
                        <FormItem
                          name={[field, 'enable']}
                          noStyle
                        >
                          <Switch disabled={isThinkingOnly && field === 'thinking'} />
                        </FormItem>
                        <Flex align="center" gap={4}>
                          {t(`workflow.config.llm.${field}`)}

                          {!firstFieldConfigs?.enable?.hideTip &&
                            <Tooltip title={t(`workflow.config.llm.${field}_tip`)}>
                              <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')] rb:shrink-0"></div>
                            </Tooltip>
                          }
                        </Flex>
                      </Flex>
                      {firstFieldConfigs.value?.type &&
                        <div className="rb:flex-1 rb:max-w-[50%] rb:overflow-hidden rb:pl-1.5!">
                          <FormItem
                            name={[field, 'value']}
                            noStyle
                          >
                            {firstFieldConfigs.value.type === 'slider'
                              ? <RbSlider
                                  {...firstFieldConfigs.value}
                                  isInput={true}
                                  inputClassName="rb:w-[100px]!"
                                  className="rb:w-full!"
                                  onChange={(value) => handleNumChange([field, 'value'], value, firstFieldConfigs.value.min)}
                                />
                              : firstFieldConfigs.value.type === 'inputNumber'
                              ? <InputNumber
                                {...firstFieldConfigs.value}
                                className="rb:w-full!"
                                onChange={(value) => handleNumChange([field, 'value'], value, firstFieldConfigs.value.min)}
                              />
                              : firstFieldConfigs.value.type === 'select'
                              ? <Select {...firstFieldConfigs.value} className="rb:w-full!" />
                              : firstFieldConfigs.value.type === 'editor'
                              ? <Editor
                                options={variableOptions}
                                type="input"
                                variant="outlined"
                                placeholder={t('common.pleaseEnter')} />
                              : null
                            }
                          </FormItem>
                        </div>
                      }
                    </Flex>
                    {firstFieldConfigs.budget &&
                      <Flex align="center" justify="space-between" wrap={false} gap={32} className="rb:mt-3!">
                        <Flex align="center" wrap={false} gap={8}>
                          <FormItem
                            name={[field, 'budget', 'enable']}
                            noStyle
                          >
                            <Switch disabled={isThinkingOnly && field === 'thinking'} />
                          </FormItem>
                          <Flex align="center" gap={4}>
                            {t(`workflow.config.llm.${field}_budget`)}

                            {!firstFieldConfigs.budget?.enable?.hideTip &&
                              <Tooltip title={t(`workflow.config.llm.${field}_budget_tip`)}>
                                <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')] rb:shrink-0"></div>
                              </Tooltip>
                            }
                          </Flex>
                        </Flex>
                        {firstFieldConfigs.budget?.value?.type &&
                          <div className="rb:flex-1 rb:max-w-[50%] rb:overflow-hidden rb:pl-1.5!">
                            <FormItem
                              name={[field, 'budget', 'value']}
                              noStyle
                            >
                              {firstFieldConfigs.budget.value.type === 'slider'
                                ? <RbSlider
                                    {...firstFieldConfigs.budget.value}
                                    isInput={true}
                                    inputClassName="rb:w-[100px]!"
                                    className="rb:w-full!"
                                    onChange={(value) => handleNumChange([field, 'budget', 'value'], value, firstFieldConfigs.budget.value.min)}
                                  />
                                : firstFieldConfigs.budget.value.type === 'inputNumber'
                                ? <InputNumber
                                  {...firstFieldConfigs.budget.value}
                                  className="rb:w-full!"
                                  onChange={(value) => handleNumChange([field, 'budget', 'value'], value, firstFieldConfigs.budget.value.min)}
                                />
                                : firstFieldConfigs.budget.value.type === 'select'
                                ? <Select
                                  {...firstFieldConfigs.budget.value}
                                  className="rb:w-full!"
                                />
                                : null
                              }
                            </FormItem>
                          </div>
                        }
                      </Flex>
                    }
                  </div>)
                  : null
                }
              </div>
            )
          })}
        </Flex>
      </Form>
    </RbModal>
  );
});

export default ModelConfigModal;