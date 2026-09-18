import type { FC } from 'react'
import { Button, Flex, Form, Select } from 'antd'
import { useTranslation } from 'react-i18next'

import type { Variable } from '@/views/ApplicationConfig/components/VariableList/types'
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import { filterChildrenWithTypes } from '../hooks/useVariableList';
import VariableSelect from '../VariableSelect';

interface AgentVariableMappingProps {
  options: Suggestion[]
  agentVariables: Variable[]
}

interface VariableMappingValue {
  name?: string
  value?: string
}
const getFilterOptions = (options: Suggestion[], type?: string) => {
  if (!type) return []
  const customMatcher = (dataType: string) =>
    (dataType === 'string' && ['text', 'paragraph'].includes(type))

  return filterChildrenWithTypes(options, [type], customMatcher)
}

const AgentVariableMapping: FC<AgentVariableMappingProps> = ({ options, agentVariables }) => {
  const { t } = useTranslation()
  const form = Form.useFormInstance()
  const mappings = Form.useWatch('variable_mapping', form) as VariableMappingValue[] | undefined
  const selectedNames = new Set((mappings ?? []).map(item => item?.name).filter(Boolean))
  const canAdd = agentVariables.some(variable => !selectedNames.has(variable.name))

  if (!agentVariables.length) {
    return null
  }
  return (
    <Form.List name='variable_mapping'>
      {(fields, { add, remove }) => (
        <Flex gap={8} vertical className="rb:mb-4!">
          <div className="rb:text-[12px] rb:font-medium rb:leading-4.5">
            {t('workflow.config.agent.variable_mapping')}
          </div>

          {fields.map(({ key, name: fieldName, ...restField }) => {
            const currentName = mappings?.[fieldName]?.name
            const currentType = agentVariables.find(variable => variable.name === currentName)?.type
            const filteredOptions = getFilterOptions(options, currentType)

            return (
              <Flex key={key} align="center" gap={4}>
                <Form.Item {...restField} name={[fieldName, 'name']} noStyle>
                  <Select
                    size="small"
                    placeholder={t('workflow.config.agent.variableNamePlaceholder')}
                    className="rb:w-32! rb:shrink-0!"
                    options={agentVariables.map(variable => ({
                      value: variable.name,
                      label: variable.display_name
                        ? `${variable.display_name} (${variable.name})`
                        : variable.name,
                      disabled: variable.name !== currentName && selectedNames.has(variable.name),
                    }))}
                    popupMatchSelectWidth={false}
                  />
                </Form.Item>
                <Form.Item {...restField} name={[fieldName, 'value']} noStyle>
                  <VariableSelect
                    options={filteredOptions}
                    allowClear={false}
                    placeholder={t('workflow.config.agent.variableValuePlaceholder')}
                    size="small"
                    className="rb:flex-1"
                  />
                </Form.Item>
                <div
                  className="rb:size-4 rb:shrink-0 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                  onClick={() => remove(fieldName)}
                />
              </Flex>
            )
          })}
          <Button
            type="dashed"
            block
            size="middle"
            className="rb:text-[12px]!"
            disabled={!canAdd}
            onClick={() => add({ name: undefined, value: '' })}
          >
            + {t('workflow.config.addVariable')}
          </Button>
        </Flex>
      )}
    </Form.List>
  )
}

export default AgentVariableMapping
