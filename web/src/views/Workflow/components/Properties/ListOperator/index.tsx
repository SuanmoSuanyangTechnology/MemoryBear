import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Form, Switch, Select, Row, Col, Divider, InputNumber } from 'antd'
import { Node } from '@antv/x6'

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'
import { fileSubVariable } from '../hooks/useVariableList'
import FilterConditions from './FilterConditions'
import RadioGroupBtn from '../RadioGroupBtn'
import RbSlider from '@/components/RbSlider'


interface ListOperatorProps {
  options: Suggestion[]
  selectedNode: Node
}

const ListOperator: FC<ListOperatorProps> = ({ options }) => {
  const { t } = useTranslation()
  const form = Form.useFormInstance()
  const values = Form.useWatch([], form) || {}
  const variableOption = options.find(option => `{{${option.value}}}` === values?.variable)
  const variableType = variableOption?.dataType

  return (
    <>
      <Form.Item name="variable" label={t('workflow.config.list-operator.variable')} required>
        <VariableSelect
          placeholder={t('common.pleaseSelect')}
          options={options.filter(vo => vo.dataType.includes('array') && vo.dataType !== 'array[object]')}
          size="small"
        />
      </Form.Item>

      <Divider />
      <Form.Item layout="horizontal" name={['filter_by', 'enabled']} label={t('workflow.config.list-operator.filter_by')} className="rb:mb-0!">
        <Switch />
      </Form.Item>
      {values?.filter_by?.enabled &&
        <FilterConditions
          variableType={variableType}
          parentName="filter_by"
          options={options}
        />
      }

      <Divider />
      <Form.Item layout="horizontal" name={['order_by', 'enabled']} label={t('workflow.config.list-operator.order_by')} className="rb:mb-0!">
        <Switch />
      </Form.Item>
      {values?.order_by?.enabled &&
        <Row gutter={8}>
          {/* 仅 array[file]有效 */}
          {variableType === 'array[file]' &&
            <Col flex="200px">
              <Form.Item name={['order_by', 'key']} className="rb:mb-0!">
                <Select
                  options={fileSubVariable}
                  fieldNames={{ value: 'filed', label: 'label' }}
                />
              </Form.Item>
            </Col>
          }
          <Col flex="1">
            <Form.Item name={['order_by', 'value']} className="rb:mb-0!">
              <RadioGroupBtn
                options={['asc', 'desc'].map(key => ({ label: t(`workflow.config.list-operator.${key}`), value: key }))}
              />
            </Form.Item>
          </Col>
        </Row>
      }

      <Divider />
      <Form.Item layout="horizontal" name={['extract_by', "enabled"]} label={t('workflow.config.list-operator.extract_by')} className="rb:mb-0!">
        <Switch />
      </Form.Item>
      {values?.extract_by?.enabled &&
        <Form.Item name={['extract_by', "serial"]} className="rb:mb-0!">
          <InputNumber placeholder={t('common.pleaseEnter')} className="rb:w-full!" />
        </Form.Item>
      }

      <Divider />
      <Form.Item layout="horizontal" name={['limit', "enabled"]} label={t('workflow.config.list-operator.limit')} className="rb:mb-2!">
        <Switch />
      </Form.Item>
      {values?.limit?.enabled &&
        <Form.Item name={['limit', "size"]} className="rb:mb-0!">
          <RbSlider
            min={1}
            max={20}
            step={1}
            isInput={true}
            size="small"
            className="rb:-mt-2!"
          />
        </Form.Item>
      }

      <Divider />
    </>
  )
}

export default ListOperator
