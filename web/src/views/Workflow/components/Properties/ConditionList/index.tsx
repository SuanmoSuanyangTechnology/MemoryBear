import { type FC } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next';
import { Form, Button, Select, Space, Row, Col, Divider } from 'antd'
import { DeleteOutlined } from '@ant-design/icons';

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'
import Editor from '../../Editor'

interface Case {
  logical_operator: 'and' | 'or';
  expressions: Array<{ left: string; comparison_operator: string; right: string; }>
}

interface CaseListProps {
  value?: Case;
  onChange?: (value: Case) => void;
  options: Suggestion[];
  parentName: string;
  selectedNode?: any;
  graphRef?: any;
  addBtnText?: string;
}
const operatorList = [
  "empty",
  "not_empty",
  "contains",
  "not_contains",
  "startwith",
  "endwith",
  "eq",
  "ne",
  "lt",
  "le",
  "gt",
  "ge"
]

const ConditionList: FC<CaseListProps> = ({
  value,
  options,
  parentName,
  onChange,
}) => {
  const { t } = useTranslation();

  const handleChangeLogicalOperator = () => {
    if (!value) return;
    onChange && onChange({
      logical_operator: value.logical_operator === 'and' ? 'or' : 'and',
      expressions: value.expressions || []
    })
  }
  return (
    <>
      <Form.List name={[parentName, 'expressions']}>
        {(fields, { add, remove }) => (
          <div>
            <div className="rb:relative">
            {fields.map((field, index) => {
              const currentOperator = value?.expressions?.[index]?.comparison_operator;
              const hideRightField = currentOperator === 'empty' || currentOperator === 'not_empty';
              
              return (
                <div key={field.key} className="rb:mb-3">
                  {index > 0 && (<>
                    <div className="rb:absolute rb:w-3 rb:left-2 rb:top-3.75 rb:bottom-3.75 rb:z-10 rb:border rb:border-[#DFE4ED] rb:rounded-l-md rb:border-r-0"></div>
                    <div className="rb:absolute rb:z-10 rb:left-0 rb:top-[50%] rb:transform-[translateY(-50%)]]">
                      <Form.Item name={[parentName, 'logical_operator']} noStyle >
                        <Button size="small" className="rb:cursor-pointer" onClick={handleChangeLogicalOperator}>{value?.logical_operator}</Button>
                      </Form.Item>
                    </div>
                  </>)}
                  
                  <div className="rb:border rb:border-[#DFE4ED] rb:rounded-md rb:p-3 rb:bg-white rb:ml-6">
                    <Row gutter={8} align="middle">
                      <Col span={14}>
                        <Form.Item name={[field.name, 'left']} noStyle>
                          <VariableSelect
                            placeholder="输入值"
                            options={options}
                            size="small"
                            allowClear={false}
                            popupMatchSelectWidth={false}
                          />
                        </Form.Item>
                      </Col>
                      
                      <Col span={8}>
                        <Form.Item name={[field.name, 'comparison_operator']} noStyle>
                          <Select
                            placeholder="包含"
                            options={operatorList.map(key => ({
                              value: key,
                              label: t(`workflow.config.if-else.${key}`)
                            }))}
                            size="small"
                            popupMatchSelectWidth={false}
                          />
                        </Form.Item>
                      </Col>
                      <Col span={2}>
                        <DeleteOutlined
                          className="rb:text-gray-400 rb:cursor-pointer rb:hover:text-red-500"
                          onClick={() => remove(field.name)}
                        />
                      </Col>
                      
                      {!hideRightField && (
                        <Col span={24}>
                          <Form.Item name={[field.name, 'right']} noStyle>
                            <Editor options={options} />
                          </Form.Item>
                        </Col>
                      )}
                      
                    </Row>
                  </div>
                </div>
              )
            })}
            </div>

            <Button
              type="dashed"
              onClick={() => add({ left: '', comparison_operator: '', right: '' })}
              className="rb:w-full rb:ml-6 rb:mt-2"
              icon={<span>+</span>}
            >
              添加条件
            </Button>
          </div>
        )}
      </Form.List>
    </>
  )
}

export default ConditionList