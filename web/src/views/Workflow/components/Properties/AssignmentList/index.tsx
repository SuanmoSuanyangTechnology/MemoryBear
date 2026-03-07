import { type FC } from 'react'
import { useTranslation } from 'react-i18next';
import { Form, Input, Select, InputNumber, Button, Flex } from 'antd'

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'
import RadioGroupBtn from '../RadioGroupBtn'

interface AssignmentListProps {
  value?: Array<{ variable_selector: string; operation: string[]; value: string;}>;
  parentName: string;
  options: Suggestion[];
  size?: 'small' | 'middle'
}

const operationsObj = {
  number: [
    { value: 'cover', label: 'workflow.config.assigner.cover' },
    { value: 'clear', label: 'workflow.config.assigner.clear' },
    { value: 'assign', label: 'workflow.config.assigner.assign' },
    { value: 'add', label: 'workflow.config.assigner.add' },
    { value: 'subtract', label: 'workflow.config.assigner.subtract' },
    { value: 'multiply', label: 'workflow.config.assigner.multiply' },
    { value: 'divide', label: 'workflow.config.assigner.divide' },
  ],
  default: [
    { value: 'cover', label: 'workflow.config.assigner.cover' },
    { value: 'clear', label: 'workflow.config.assigner.clear' },
    { value: 'assign', label: 'workflow.config.assigner.assign' },
  ],
}

const AssignmentList: FC<AssignmentListProps> = ({
  parentName,
  options = [],
  size = 'small'
}) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();

  return (
    <Form.List name={parentName}>
      {(fields, { add, remove }) => (
        <>
          <Flex align="center" justify="space-between" className="rb:mb-2.5!">
            <div className="rb:text-[12px] rb:leading-4.5 rb:font-medium">
              {t(`workflow.config.assigner.${parentName}`)}
            </div>

            <Button
              onClick={() => add({ operation: 'cover' })}
              className="rb:py-0! rb:px-1! rb:h-4.5! rb:rounded-sm! rb:text-[12px]!"
              size="small"
            >
              + {t('workflow.config.addVariable')}
            </Button>
          </Flex >

          <Flex gap={10} vertical>
            {fields.map(({ key, name, ...restField }) => {
              const variableSelector = form.getFieldValue([parentName, name, 'variable_selector']);
              const selectedOption = options.find(option => `{{${option.value}}}` === variableSelector);
              const dataType = selectedOption?.dataType;
              const operationOptions = dataType === 'number' ? operationsObj.number : operationsObj.default;
              
              return (
                <Flex key={key} gap={4} align="start">
                  <div className="rb:flex-1">
                    <Flex gap={4} className="rb:mb-1!">
                      <Form.Item
                        {...restField}
                        name={[name, 'variable_selector']}
                        noStyle
                      >
                        <VariableSelect
                          placeholder={t('common.pleaseSelect')}
                          options={options.filter(vo => vo.nodeData.type === 'loop' || vo.value.includes('conv.') || (vo.nodeData.type === 'iteration' && (vo.label === 'item' || vo.label === 'index')))}
                          popupMatchSelectWidth={false}
                          onChange={() => {
                            form.setFieldValue([parentName, name, 'operation'], undefined);
                            form.setFieldValue([parentName, name, 'value'], undefined);
                          }}
                          size={size}
                          className="rb:w-39! rb:bg-[#F6F6F6]!"
                          variant="borderless"
                        />
                      </Form.Item>
                      <Form.Item
                        {...restField}
                        name={[name, 'operation']}
                        noStyle
                      >
                        <Select
                          placeholder={t('common.pleaseSelect')}
                          options={operationOptions.map(op => ({
                            ...op,
                            label: t(op.label)
                          }))}
                          popupMatchSelectWidth={false}
                          onChange={() => {
                            form.setFieldValue([parentName, name, 'value'], undefined);
                          }}
                          size={size}
                          className="rb:w-39! select"
                          variant="borderless"
                        />
                      </Form.Item>
                    </Flex>
                    <Form.Item shouldUpdate noStyle>
                      {(form) => {
                        const operation = form.getFieldValue([parentName, name, 'operation']);
                        if (operation === 'clear') return null;

                        return (
                          <Form.Item
                            {...restField}
                            name={[name, 'value']}
                            noStyle
                          >
                            {dataType === 'number' && operation === 'cover'
                              ? <VariableSelect
                                placeholder={t('common.pleaseSelect')}
                                options={dataType ? options.filter(vo => vo.dataType === dataType) : options}
                                popupMatchSelectWidth={false}
                                size={size}
                                variant="borderless"
                                className="select"
                              />
                              : dataType === 'number'
                                ? <InputNumber
                                  placeholder={t('common.pleaseEnter')}
                                  className="rb:w-full! rb:bg-[#F6F6F6]!"
                                  onChange={(value) => form.setFieldValue([name, 'value'], value)}
                                  size={size}
                                  variant="borderless"
                                />
                                : operation === 'assign'
                                  ? <>
                                    {dataType === 'boolean'
                                      ? <RadioGroupBtn
                                        options={[
                                          { value: true, label: 'True' },
                                          { value: false, label: 'False' }]}
                                      />
                                      : <Input.TextArea
                                        placeholder={t('common.pleaseEnter')}
                                        rows={3}
                                        variant="borderless"
                                        className="rb:bg-[#F6F6F6]!"
                                      />
                                    }
                                  </>
                                  : <VariableSelect
                                    placeholder={t('common.pleaseSelect')}
                                    options={dataType ? options.filter(vo => vo.dataType === dataType) : options}
                                    popupMatchSelectWidth={false}
                                    size={size}
                                    variant="borderless"
                                    className="select"
                                  />
                            }
                          </Form.Item>
                        );
                      }}
                    </Form.Item>
                  </div>
                  <div
                    className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                    onClick={() => remove(name)}
                  ></div>
                </Flex>
              )
            })}
          </Flex>
        </>
      )}
    </Form.List>
  )
}

export default AssignmentList