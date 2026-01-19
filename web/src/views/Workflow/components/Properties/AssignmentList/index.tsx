import { type FC } from 'react'
import { useTranslation } from 'react-i18next';
import { Form, Input, Select, InputNumber, Radio, Button, Space } from 'antd'
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'

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
          <div className="rb:flex rb:items-center rb:justify-between rb:mb-2.5">
            <div className="rb:text-[12px] rb:leading-4.5 rb:font-medium">
              {t(`workflow.config.assigner.${parentName}`)}
            </div>

            <Button
              onClick={() => add({ operation: 'cover' })}
              className="rb:py-0! rb:px-1! rb:text-[12px]!"
              size="small"
            >
              + {t('workflow.config.addVariable')}
            </Button>
          </div>

          <Space size={10} direction="vertical" className="rb:w-full!">
            {fields.map(({ key, name, ...restField }) => {
              const variableSelector = form.getFieldValue([parentName, name, 'variable_selector']);
              const selectedOption = options.find(option => `{{${option.value}}}` === variableSelector);
              const dataType = selectedOption?.dataType;
              const operationOptions = dataType === 'number' ? operationsObj.number : operationsObj.default;
              
              return (
                <div key={key} className="rb:flex rb:items-start">
                  <div className="rb:flex-1">
                    <div className="rb:flex rb:gap-1 rb:mb-1">
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
                          className="rb:w-39!"
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
                          className="rb:w-24!"
                        />
                      </Form.Item>
                    </div>
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
                              />
                              : dataType === 'number'
                                ? <InputNumber
                                  placeholder={t('common.pleaseEnter')}
                                  className="rb:w-full!"
                                  onChange={(value) => form.setFieldValue([name, 'value'], value)}
                                  size={size}
                                />
                                : operation === 'assign'
                                  ? <>
                                    {dataType === 'boolean'
                                      ? <Radio.Group block size={size}>
                                        <Radio.Button value={true}>True</Radio.Button>
                                        <Radio.Button value={false}>False</Radio.Button>
                                      </Radio.Group>
                                      : <Input.TextArea
                                        placeholder={t('common.pleaseEnter')}
                                        rows={3}
                                      />
                                    }
                                  </>
                                  : <VariableSelect
                                    placeholder={t('common.pleaseSelect')}
                                    options={dataType ? options.filter(vo => vo.dataType === dataType) : options}
                                    popupMatchSelectWidth={false}
                                    size={size}
                                  />
                            }
                          </Form.Item>
                        );
                      }}
                    </Form.Item>
                  </div>
                  <div
                    className="rb:ml-1 rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                    onClick={() => remove(name)}
                  ></div>
                </div>
              )
            })}
          </Space>
        </>
      )}
    </Form.List>
  )
}

export default AssignmentList