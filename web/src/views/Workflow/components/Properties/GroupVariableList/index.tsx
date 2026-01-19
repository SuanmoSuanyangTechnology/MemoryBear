import { type FC } from 'react'
import { useTranslation } from 'react-i18next';
import { Form, Input, Button, Row, Col } from 'antd'

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'

interface GroupVariableListProps {
  value?: Array<{ key: string; value: string[]; }>;
  name: string;
  options: Suggestion[];
  isCanAdd: boolean;
  size: 'small' | 'middle'
}

const GroupVariableList: FC<GroupVariableListProps> = ({
  name,
  options = [],
  isCanAdd = false,
  size = "middle"
}) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();
  const value = form.getFieldValue(name) || [];

  console.log('GroupVariableList', value)

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
      <div className="rb:mb-4">
        <Row gutter={12} className="rb:mb-2!">
          <Col span={12}>
            <Form.Item
              noStyle
            >
              {t('workflow.config.var-aggregator.variable')}
            </Form.Item>
          </Col>
        </Row>

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
      </div>
    )
  }
  return (
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
  )
}

export default GroupVariableList