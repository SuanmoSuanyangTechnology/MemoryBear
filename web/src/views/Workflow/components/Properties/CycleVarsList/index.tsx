import { type FC, useMemo } from 'react'
import { useTranslation } from 'react-i18next';
import { Form, Select, Input, Button, InputNumber, Flex } from 'antd'

import VariableSelect from '../VariableSelect'
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import RadioGroupBtn from '../RadioGroupBtn'
import { getChildNodeVariables } from '../hooks/useVariableList'
import CodeMirrorEditor from '@/components/CodeMirrorEditor';

interface CycleVar {
  name: string;
  type: string;
  value: string;
  input_type: string;
}

interface CycleVarsListProps {
  value?: CycleVar[];
  onChange?: (value: CycleVar[]) => void;
  options: Suggestion[];
  parentName: string;
  selectedNode?: any;
  graphRef?: any;
  size?: 'small' | 'middle'
}

const types = [
  'string',
  'number',
  'boolean',
  'object',
  'array[string]',
  'array[number]',
  'array[boolean]',
  'array[object]'
]
const object_placeholder = `# example
# {
#   "name": "redbear",
#   "age": 2
# }`

const CycleVarsList: FC<CycleVarsListProps> = ({
  value = [],
  options,
  parentName,
  selectedNode,
  graphRef,
  size = 'middle'
}) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();

  const availableOptions = useMemo(() => {
    if (!selectedNode || !graphRef?.current || selectedNode.getData()?.type !== 'loop') {
      return options;
    }
    const childVars = getChildNodeVariables(selectedNode, graphRef);

    return options.filter(option => !childVars.some(item => item.value === option.value))
  }, [options, selectedNode, graphRef]);

  return (
    <Form.List name={parentName}>
      {(fields, { add, remove }) => (
        <Flex vertical gap={8}>
          <Flex align="center" justify="space-between">
            <span className="rb:text-[12px] rb:font-medium">{t('workflow.config.loop.cycle_vars')}</span>
            <Button
              onClick={() => add({ name: '', type: 'string', input_type: 'constant', value: '' })}
              className="rb:py-0! rb:px-1! rb:h-4.5! rb:rounded-sm! rb:text-[12px]!"
              size="small"
            >
              + {t('workflow.config.addVariable')}
            </Button>
          </Flex>
          <Flex vertical gap={12}>
            {fields.map(({ key, name }, index) => {
              const currentType = value?.[index]?.type;
              const currentInputType = value?.[index]?.input_type;
              
              return (
                <Flex key={key} gap={4}>
                  <Flex vertical gap={4}>
                    <Flex gap={4}>
                      <Form.Item name={[name, 'name']} noStyle>
                        <Input
                          size={size}
                          className="rb:w-25.5! rb:bg-[#F6F6F6]!"
                          variant="borderless"
                          placeholder={t('common.pleaseEnter')}
                        />
                      </Form.Item>
                      <Form.Item name={[name, 'type']} noStyle>
                        <Select
                          options={types.map(key => ({
                            value: key,
                            label: t(`workflow.config.parameter-extractor.${key}`),
                          }))}
                          size={size}
                          popupMatchSelectWidth={false}
                          className={`rb:w-25.5! select`}
                          variant="borderless"
                        />
                      </Form.Item>
                      <Form.Item name={[name, 'input_type']} noStyle>
                        <Select
                          placeholder="Constant"
                          options={[
                            { label: 'Constant', value: 'constant' },
                            { label: 'Variable', value: 'variable' }
                          ]}
                          size={size}
                          popupMatchSelectWidth={false}
                          onChange={() => {
                            form.setFieldValue([parentName, index, 'value'], undefined);
                          }}
                          className={`rb:w-25! select`}
                          variant="borderless"
                        />
                      </Form.Item>
                    </Flex>
                    
                    <Form.Item name={[name, 'value']} noStyle >
                      {currentInputType === 'variable'
                        ? (
                          <VariableSelect
                            placeholder={t('common.pleaseSelect')}
                            options={availableOptions.filter(option => {
                              const currentType = value?.[index]?.type;
                              if (!currentType) return true;

                              return option.dataType === currentType
                            })}
                            variant="borderless"
                            size="small"
                            className="select"
                          />
                        )
                        : currentType === 'number'
                        ? <InputNumber
                          placeholder={t('common.pleaseEnter')}
                          variant="borderless"
                          className="rb:w-full! rb:bg-[#F6F6F6]!"
                          onChange={(value) => form.setFieldValue([name, 'value'], value)}
                        />
                        : currentType === 'boolean'
                        ? <RadioGroupBtn
                          options={[
                            { value: true, label: 'True' },
                            { value: false, label: 'False' }]}
                        />
                        : currentType === 'object'
                          ? <CodeMirrorEditor
                            language="json"
                            placeholder={object_placeholder}
                            variant="outlined"
                            size="small"
                          />
                        : (
                          <Input.TextArea
                            placeholder={t('common.pleaseEnter')}
                            rows={3}
                            className="rb:w-full rb:bg-[#F6F6F6]!"
                            variant="borderless"
                          />
                        )
                      }
                    </Form.Item>
                  </Flex>
                  <div
                    className="rb:mt-1.5 rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                    onClick={() => remove(name)}
                  ></div>
                </Flex>
              )
            })}
          </Flex>
        </Flex>
      )}
    </Form.List>
  )
}

export default CycleVarsList