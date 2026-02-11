/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 15:17:39 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 15:17:39 
 */
import { useEffect, type FC } from 'react'
import { useTranslation } from 'react-i18next';
import { Form, Input, Button, Row, Col } from 'antd'

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'

/**
 * Props for GroupVariableList component
 */
interface GroupVariableListProps {
  /** Current value - array of key-value pairs for grouped variables */
  value?: Array<{ key: string; value: string[]; }>;
  /** Form field name */
  name: string;
  /** Available variable options for selection */
  options: Suggestion[];
  /** Whether user can add custom groups */
  isCanAdd: boolean;
  /** Size of form controls */
  size: 'small' | 'middle'
}

/**
 * GroupVariableList component
 * Manages grouped variable selection for var-aggregator node
 * Supports two modes:
 * 1. Simple mode (isCanAdd=false): Single variable list with type inference
 * 2. Advanced mode (isCanAdd=true): Multiple named groups with type inference per group
 * @param props - Component props
 */
const GroupVariableList: FC<GroupVariableListProps> = ({
  name,
  options = [],
  isCanAdd = false,
  size = "small"
}) => {
  // Hooks
  const { t } = useTranslation();
  const form = Form.useFormInstance();
  
  // Get current form value
  const value = form.getFieldValue(name) || [];

  /**
   * Reset group_type when mode changes
   */
  useEffect(() => {
    form.setFieldValue('group_type', {})
  }, [isCanAdd])

  /**
   * Auto-infer and set data types based on selected variables
   * In simple mode: Sets single output type
   * In advanced mode: Sets type for each group
   */
  useEffect(() => {
    if (!isCanAdd && value[0]) {
      const firstVariable = options.find(opt => `{{${opt.value}}}` === value[0]);
      if (firstVariable) {
        form.setFieldValue(['group_type', 'output'], firstVariable.dataType);
      }
    } else if (isCanAdd) {
      value.forEach((item: any, index: number) => {
        if (item?.value?.[0]) {
          const firstVariable = options.find(opt => `{{${opt.value}}}` === item.value[0]);
          if (firstVariable) {
            form.setFieldValue(['group_type', index], firstVariable.dataType);
          }
        }
      });
    }
  }, [isCanAdd, options, value, form])

  /**
   * Simple mode rendering
   * Single variable list with automatic type filtering
   */
  if (!isCanAdd) {
    // Filter options based on first variable's dataType if value exists
    let filteredOptions = options;
    if (value.length > 0) {
      const firstVariableValue = value[0];
      const firstVariable = options.find(opt => `{{${opt.value}}}` === firstVariableValue);
      if (firstVariable) {
        filteredOptions = options.filter(opt => opt.dataType === firstVariable.dataType);
      }
    }
    
    return (
      <div>
        <div className="rb:font-medium rb:text-[12px] rb:mb-1">
          {t('workflow.config.var-aggregator.variable')}
        </div>

        <Form.Item
          name={name}
          noStyle
        >
          <VariableSelect
            placeholder={t('common.pleaseSelect')}
            options={filteredOptions}
            mode="multiple"
            size={size}
          />
        </Form.Item>
        <Form.Item name={['group_type', 'output']} hidden></Form.Item>
      </div>
    )
  }
  /**
   * Advanced mode rendering
   * Multiple named groups with individual variable lists
   */
  return (
    <>
      <Form.List name={name}>
        {(fields, { add, remove }) => (
          <>
            {fields.map(({ key, name, ...restField }) => {
              return (
                <div key={key} className="rb:mb-4">
                  <Row gutter={12} className="rb:mb-2!">
                    <Col span={12}>
                      <Form.Item
                        {...restField}
                        name={isCanAdd ? [name, 'key'] : undefined}
                        rules={[
                          { pattern: /^[a-zA-Z_][a-zA-Z0-9_]*$/, message: t('workflow.config.var-aggregator.invalidVariableName') },
                        ]}
                        noStyle
                      >
                        {isCanAdd ? <Input placeholder={t('common.pleaseEnter')} size={size} /> : t('workflow.config.var-aggregator.variable')}
                      </Form.Item>
                    </Col>

                    {isCanAdd && <Col span={12} className="rb:flex! rb:items-center rb:justify-end">
                      <div
                        className="rb:ml-1 rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                        onClick={() => remove(name)}
                      ></div>
                    </Col>}
                  </Row>

                  <Form.Item
                    {...restField}
                    name={[name, 'value']}
                    noStyle
                  >
                    <VariableSelect
                      placeholder={t('common.pleaseSelect')}
                      options={(() => {
                        const currentGroupValue = value[name]?.value || [];
                        if (currentGroupValue.length > 0) {
                          const firstVariableValue = currentGroupValue[0];
                          const firstVariable = options.find(opt => `{{${opt.value}}}` === firstVariableValue);
                          if (firstVariable) {
                            return options.filter(opt => opt.dataType === firstVariable.dataType);
                          }
                        }
                        return options;
                      })()
                      }
                      mode="multiple"
                      size={size}
                    />
                  </Form.Item>
                </div>
              )
            })}

            {isCanAdd && <Button 
              type="dashed" 
              block
              size="middle"
              className="rb:text-[12px]!"
              onClick={() => add({ key: `Group${fields.length + 1}` })}
            >
              + {t('workflow.config.var-aggregator.addGroup')}
            </Button>}
          </>
        )}
      </Form.List>
      <Form.Item name={['group_type']} hidden></Form.Item>
    </>
  )
}

export default GroupVariableList