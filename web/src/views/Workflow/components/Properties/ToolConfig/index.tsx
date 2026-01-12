import { type FC, useEffect, useState } from "react";
import { useTranslation } from 'react-i18next'
import { Form, Select, InputNumber, Switch, Cascader, type CascaderProps } from 'antd'
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import { getToolMethods, getToolDetail, getTools } from '@/api/tools'
import type { ToolType, ToolItem } from '@/views/ToolManagement/types'
import Editor from "../../Editor";

interface Option {
  value?: string | number | null;
  label?: React.ReactNode;
  children?: Option[];
  isLeaf?: boolean;
  method_id?: string;
  parameters?: Parameter[];
}
interface Parameter {
  name: string;
  type: string;
  description: string;
  required: boolean;
  default: any;
  enum: null | string[];
  minimum: number;
  maximum: number;
  pattern: null | string;
}


const ToolConfig: FC<{ options: Suggestion[]; }> = ({
  options,
}) => {
  const { t } = useTranslation()
  const form = Form.useFormInstance();
  const values = Form.useWatch([], form) || {}
  const [optionList, setOptionList] = useState<Option[]>([
    { value: 'mcp', label: t('tool.mcp'), isLeaf: false },
    { value: 'builtin', label: t('tool.inner'), isLeaf: false },
    { value: 'custom', label: t('tool.custom'), isLeaf: false },
  ])
  const [parameters, setParameters] = useState<Parameter[]>([])

  useEffect(() => {
    if (values.tool_id) {
      getToolDetail(values.tool_id)
        .then(res => {
          const detail = res as { tool_type: ToolType; }
          
          getTools({ tool_type: detail.tool_type })
            .then(toolsRes => {
              const tools = toolsRes as ToolItem[]
              
              getToolMethods(values.tool_id)
                .then(methodsRes => {
                  const response = methodsRes as Array<{ method_id: string; name: string; parameters: Parameter[] }>
                  
                  setOptionList(prevList => {
                    return prevList.map(item => {
                      if (item.value === detail.tool_type) {
                        return {
                          ...item,
                          children: tools.map((vo: ToolItem) => ({
                            value: vo.id,
                            label: vo.name,
                            isLeaf: false,
                            children: vo.id === values.tool_id ? response.map(method => ({
                              value: method.name,
                              label: method.name,
                              isLeaf: true,
                              method_id: method.method_id,
                              parameters: method.parameters
                            })) : undefined
                          }))
                        }
                      }
                      return item
                    })
                  })
                  
                  if (response.length > 1) {
                    const filterTarget = response.find(vo => vo.name === values.tool_parameters?.operation)
                    if (filterTarget) {
                      setParameters([...filterTarget.parameters])
                    } else {
                      setParameters([])
                    }
                  } else {
                    setParameters([...response[0].parameters])
                  }

                  form.setFieldValue('tools', [detail.tool_type, values.tool_id, values.tool_parameters?.operation ?? response[0].name])
                })
            })
        })
    }
  }, [values.tool_id, values.tool_parameters?.operation]);

  useEffect(() => {
    if (values.tools && values.tools.length === 3) {
      const [toolType, toolId, operation] = values.tools
      
      // 从 optionList 中查找对应的参数
      const typeOption = optionList.find(opt => opt.value === toolType)
      if (typeOption?.children) {
        const toolOption = typeOption.children.find(opt => opt.value === toolId)
        if (toolOption?.children) {
          const methodOption = toolOption.children.find(opt => opt.value === operation)
          if (methodOption?.parameters) {
            setParameters([...methodOption.parameters])
          }
        }
      }
    }
  }, [values.tools])

  const loadData = (selectedOptions: Option[]) => {
    const targetOption = selectedOptions[selectedOptions.length - 1];
    if (selectedOptions.length === 1) {
      getTools({ tool_type: targetOption.value as ToolType })
        .then(res => {
          const response = res as ToolItem[]
          targetOption.children = response.map((vo: any) => {
            return {
              value: vo.id,
              label: vo.name,
              isLeaf: response.length === 0,
            }
          })
          setOptionList([...optionList])
        })
    } else {
      getToolMethods(targetOption.value as string)
        .then(res => {
          const response = res as Array<{ method_id: string; name: string }>
          targetOption.children = response.map((vo: any) => {
            return {
              value: vo.name,
              label: vo.name,
              isLeaf: true,
              method_id: vo.method_id,
              parameters: vo.parameters
            }
          })
          setOptionList([...optionList])
        })
    }
  };

  const handleChange: CascaderProps<Option>['onChange'] = (value, selectedOptions) => {
    const targetOption = selectedOptions[selectedOptions.length - 1];
    const curParameters = [...(targetOption.parameters ?? [])]
    setParameters([...curParameters])
    const inititalValue: any = { tool_id: selectedOptions[1].value, tool_parameters: {} }

    if (value[0] === 'mcp' || (value[0] === 'builtin' && selectedOptions[1]?.children && selectedOptions[1].children.length > 1)) {
      inititalValue.tool_parameters.operation = value?.[2]
    } else if (value[0] === 'custom') {
      inititalValue.tool_parameters.operation = selectedOptions?.[2].method_id
    }
    curParameters.forEach(vo => {
      inititalValue.tool_parameters[vo.name] = vo.default
    })

    form.setFieldsValue(inititalValue)
  }

  return (
    <>
      <Form.Item
        name="tools"
        label={t('workflow.config.tool.tool_id')}
      >
        <Cascader 
          options={optionList} 
          loadData={loadData}
          onChange={handleChange}
          changeOnSelect={false}
        />
      </Form.Item>
      <Form.Item name="tool_id" hidden />
      <Form.Item name={['tool_parameters', 'operation']} hidden />
      {parameters.map((parameter) => {
        return (
          <div key={parameter.name}>
            <Form.Item
              name={['tool_parameters', parameter.name]}
              label={parameter.name}
              extra={parameter.type === 'boolean' ? undefined : parameter.description}
              rules={[
                { required: parameter.required, message: t('workflow.config.tool.required') }
              ]}
              layout={parameter.type === 'boolean' ? 'horizontal' : 'vertical'}
              className={parameter.type === 'boolean' ? 'rb:mb-0!' : ''}
            >
              {parameter.type === 'string' && parameter.enum && parameter.enum.length > 0
                ? <Select options={parameter.enum.map(vo => ({ value: vo, label: vo }))} placeholder={t('common.pleaseSelect')} />
                : parameter.type === 'boolean'
                ? <Switch />
                : parameter.type === 'integer' || parameter.type === 'number'
                ? <InputNumber
                    min={parameter.minimum}
                    max={parameter.maximum}
                    step={parameter.type === 'integer' ? 1 : 0.01}
                    placeholder={t('common.pleaseEnter')}
                    className="rb:w-full!"
                    onChange={(value) => form.setFieldValue(['tool_parameters', parameter.name], value)}
                  />
                : <Editor
                    height={32}
                    variant="outlined"
                    options={options} 
                    placeholder={t('common.pleaseEnter')}
                  />
              }
            </Form.Item>
            {parameter.type === 'boolean' && <div className="rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4 rb:mb-6">{parameter.description}</div>}
          </div>
        )
      })}
    </>
  );
};
export default ToolConfig;