import { forwardRef, useImperativeHandle, useState } from "react";
import { useTranslation } from 'react-i18next';
import { Form, Button, Select, Input, Space, Row, Col, type SelectProps, Flex, Divider, InputNumber } from 'antd';
import clsx from 'clsx';

import RbModal from '@/components/RbModal';
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'
import { getPublicMetadataFields } from '@/api/knowledgeBase';
import type { MetadataField } from '@/views/KnowledgeBase/types'
import Tag from '@/components/Tag'

export interface MetadataFilterModalRef {
  open: (metadata_filters: { conditions: FilterCondition[], logic: 'or' | 'and' }) => void;
  close: () => void;
}

interface MetadataFilterModalProps {
  options: Suggestion[];
  onSave: (metadata_filters: { conditions: FilterCondition[], logic: 'or' | 'and' }) => void;
  kb_ids: string[];
}

export interface FilterCondition {
  field: string;
  operator: string;
  value_type: 'constant' | 'variable';
  value: string;
}

const operatorsObj: { [key: string]: SelectProps['options'] } = {
  default: [
    { value: 'eq', label: 'workflow.config.if-else.eq' },
    { value: 'ne', label: 'workflow.config.if-else.ne' },
    { value: 'contains', label: 'workflow.config.if-else.contains' },
    { value: 'not_contains', label: 'workflow.config.if-else.not_contains' },
    { value: 'start_with', label: 'workflow.config.knowledge-retrieval.start_with' },
    { value: 'end_with', label: 'workflow.config.knowledge-retrieval.end_with' },
    { value: 'is_empty', label: 'workflow.config.knowledge-retrieval.is_empty' },
    { value: 'not_empty', label: 'workflow.config.if-else.not_empty' },
  ],
  number: [
    { value: 'eq', label: 'workflow.config.if-else.num.eq' },
    { value: 'ne', label: 'workflow.config.if-else.num.ne' },
    { value: 'gt', label: 'workflow.config.if-else.num.gt' },
    { value: 'lt', label: 'workflow.config.if-else.num.lt' },
    { value: 'lte', label: 'workflow.config.knowledge-retrieval.lte' },
    { value: 'gte', label: 'workflow.config.knowledge-retrieval.gte' },
    { value: 'empty', label: 'workflow.config.if-else.empty' },
    { value: 'not_empty', label: 'workflow.config.if-else.not_empty' },
  ],
  time: [
    { value: 'eq', label: 'workflow.config.if-else.eq' },
    { value: 'before', label: 'workflow.config.knowledge-retrieval.before' },
    { value: 'after', label: 'workflow.config.knowledge-retrieval.after' },
    { value: 'is_empty', label: 'workflow.config.knowledge-retrieval.is_empty' },
    { value: 'not_empty', label: 'workflow.config.if-else.not_empty' },
  ],
}
const inputTypeOptions = [
  { value: 'constant', label: 'Constant' },
  { value: 'variable', label: 'Variable' },
]


const MetadataFilterModal = forwardRef<MetadataFilterModalRef, MetadataFilterModalProps>(({
  options,
  onSave,
  kb_ids = []
}, ref) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const [logicalOperator, setLogicalOperator] = useState<'or' | 'and'>('and');
  const values = Form.useWatch([], form);
  const conditions = Form.useWatch(['metadata_filters', 'conditions'], form);
  console.log('MetadataFilterModal values', values)

  const [metadataFields, setMetadataFields] = useState<MetadataField[]>([]);

  useImperativeHandle(ref, () => ({
    open: handleOpen,
    close: handleCancel
  }));
  const handleCancel = () => {
    setOpen(false);
    form.resetFields();
  }
  const handleOpen = (metadata_filters: { conditions: FilterCondition[], logic: 'or' | 'and' }) => {
    form.setFieldsValue({
      metadata_filters
    });
    setLogicalOperator(metadata_filters.logic || 'and');
    setOpen(true);

    if (kb_ids?.length) {
      getPublicMetadataFields({ kb_ids })
        .then(res => {
          const { custom, builtin_fields } = res as { custom: MetadataField[], builtin_fields: MetadataField[] };
          setMetadataFields([...custom, ...builtin_fields]);
        })
    }
  }

  const handleSave = () => {
    form.validateFields().then((values) => {
      const { metadata_filters } = values;
      const validFilters = metadata_filters?.conditions?.filter((v: FilterCondition) => v.field && v.value);
      onSave({ conditions: validFilters, logic: logicalOperator });
      handleCancel();
    });
  };

  const handleLeftFieldChange = (index: number, newValue?: string | string[]) => {
    const lastFilter = conditions[index];
    form.setFieldsValue({
      metadata_filters: {
        [index]: {
          ...conditions[index],
          operator: 'eq',
          field: newValue,
          value: lastFilter.value_type === 'variable' ? undefined : lastFilter.value
        }
      }
    });
  };

  const handleInputTypeChange = (index: number) => {
    form.setFieldValue(['metadata_filters', index, 'value'], undefined);
  };

  const handleChangeLogicalOperator = () => {
    setLogicalOperator(prev => prev === 'and' ? 'or' : 'and');
  };

  console.log('logicalOperator', logicalOperator)

  return (
    <RbModal
      title={t('workflow.config.knowledge-retrieval.metadata')}
      open={open}
      onCancel={handleCancel}
      onOk={handleSave}
      okText={t('common.confirm')}
    >
      <Form
        form={form}
        size="middle"
      >
        <Form.List name={["metadata_filters", "conditions"]}>
          {(fields, { add, remove }) => {
            return (
              <div className="rb:mb-4!">
                <div
                  className={clsx("rb:relative", {
                    'rb:ml-15!': fields?.length > 1
                  })}
                >
                  {fields?.length > 1 && (
                    <div className="rb:absolute rb:-left-9 rb:top-4 rb:bottom-4 rb:w-6 rb:h-[calc(100%-32px)]">
                      <div className="rb:absolute rb:w-3 rb:h-[calc(50%-20px)] rb:left-5 rb:top-0 rb:z-10 rb:border-l rb:border-t rb:border-[#EBEBEB] rb:rounded-tl-[10px] rb:border-r-0"></div>
                      <div className="rb:absolute rb:z-10 rb:-right-1.25 rb:top-[calc(50%-10px)]">
                        <Space
                          size={2}
                          className="rb:cursor-pointer rb:text-[#155EEF] rb:leading-4.5 rb:font-medium rb-border rb:py-px! rb:px-1! rb:rounded-sm"
                          onClick={handleChangeLogicalOperator}
                        >
                          {logicalOperator}
                          <div className="rb:size-3 rb:bg-cover rb:bg-[url('@/assets/images/workflow/refresh_active.svg')]"></div>
                        </Space>
                      </div>
                      <div className="rb:absolute rb:w-3 rb:h-[calc(50%-20px)] rb:left-5 rb:bottom-0 rb:z-10 rb:border-l rb:border-b rb:border-[#EBEBEB] rb:rounded-bl-[10px] rb:border-r-0"></div>
                    </div>
                  )}
                  {fields.map((field, index) => {
                    const currentCondition = conditions?.[index] || {};
                    const currentOperator = currentCondition.operator;
                    const leftFieldValue = currentCondition.field;
                    const leftFieldOption = options.find(option => `{{${option.value}}}` === leftFieldValue)
                      ?? options.flatMap(o => o.children ?? []).find(child => `{{${child.value}}}` === leftFieldValue)
                      ?? options.flatMap(o => o.children ?? []).flatMap((c: any) => c.children ?? []).find((gc: any) => `{{${gc.value}}}` === leftFieldValue);
                    const leftFieldType = leftFieldOption?.dataType;
                    const hideRightField = currentOperator === 'is_empty' || currentOperator === 'not_empty';
                    const operatorList = leftFieldType && ['array[object]', 'object'].includes(leftFieldType)
                      ? operatorsObj.object
                      : leftFieldType && ['array[boolean]', 'boolean'].includes(leftFieldType)
                      ? operatorsObj.boolean
                      : leftFieldType && operatorsObj[leftFieldType]
                      ? operatorsObj[leftFieldType]
                      : leftFieldType?.includes('array')
                      ? operatorsObj.array
                      : operatorsObj.default
                    const valueType = leftFieldType === 'number' ? currentCondition.value_type : undefined;
                    return (
                      <Flex
                        key={field.key} 
                        gap={4} 
                        align="start" 
                        className="rb:mb-2!"
                      >
                        <div className="rb:flex-1 rb:bg-[#F6F6F6] rb:rounded-lg">
                          <Row wrap={false} className={clsx("rb:px-1!", {
                            'rb-border-b': !hideRightField
                          })}>
                            <Col flex="1">
                              <Form.Item name={[field.name, 'field']} noStyle>
                                <Select
                                  options={metadataFields.map(item => ({
                                    value: item.name,
                                    label: <Space>{item.name}<Tag>{item.type}</Tag></Space>
                                  }))}
                                  allowClear={false}
                                  placeholder={t('common.pleaseSelect')}
                                  onChange={(val) => handleLeftFieldChange(index, val)}
                                  variant="borderless"
                                  className="rb:w-full!"
                                />
                              </Form.Item>
                            </Col>
                            <Col flex="96px">
                              <Form.Item name={[field.name, 'operator']} noStyle>
                                <Select
                                  options={(operatorList??[]).map(vo => ({
                                    ...vo,
                                    label: t(String(vo?.label || ''))
                                  }))}
                                  popupMatchSelectWidth={false}
                                  placeholder={t('common.pleaseSelect')}
                                  variant="borderless"
                                  className="rb:w-full!"
                                />
                              </Form.Item>
                            </Col>
                          </Row>
                          
                          {!hideRightField && (
                            <div>
                              <Flex align="center">
                                <Form.Item name={[field.name, 'value_type']} noStyle>
                                  <Select
                                    placeholder={t('common.pleaseSelect')}
                                    options={inputTypeOptions}
                                    popupMatchSelectWidth={false}
                                    variant="borderless"
                                    className="rb:w-30!"
                                    onChange={() => handleInputTypeChange(index)}
                                  />
                                </Form.Item>
                                <Divider type="vertical" />
                                <Form.Item name={[field.name, 'value']} noStyle>
                                  {valueType === 'variable'
                                    ? (
                                      <VariableSelect
                                        placeholder={t('common.pleaseSelect')}
                                        options={options}
                                        allowClear={false}
                                        variant="borderless"
                                      />
                                    )
                                    : leftFieldType === 'number' ? (
                                      <InputNumber
                                        placeholder={t('common.pleaseEnter')}
                                        variant="borderless"
                                        className="rb:w-full!"
                                        onChange={(value) => form.setFieldValue(['metadata_filters', index, 'value'], value)}
                                      />
                                    )
                                    : (
                                      <Input
                                        placeholder={t('common.pleaseEnter')}
                                        variant="borderless"
                                      />
                                    )
                                  }
                                </Form.Item>
                              </Flex>
                            </div>
                          )}
                        </div>
                        <div
                          className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                          onClick={() => remove(field.name)}
                        ></div>
                      </Flex>
                    )
                  })}
                </div>
                <Flex align="center" justify="space-between" className="rb:mb-2!">
                  <Button
                    type="dashed"
                    size="middle"
                    block
                    onClick={() => add({
                      operator: 'eq',
                      value_type: 'constant',
                    })}
                  >
                    + {t('workflow.config.knowledge-retrieval.addFilter')}
                  </Button>
                </Flex>
              </div>
            )
          }}
        </Form.List>
      </Form>
    </RbModal>
  );
});

export default MetadataFilterModal;
