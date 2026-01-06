import { type FC } from 'react'
import { useTranslation } from 'react-i18next';
import { Form, Input, Button, Row, Col } from 'antd'
import { MinusCircleOutlined } from '@ant-design/icons';
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'

interface GroupVariableListProps {
  value?: Array<{ key: string; value: string[]; }>;
  name: string;
  options: Suggestion[];
  isCanAdd: boolean
}

const GroupVariableList: FC<GroupVariableListProps> = ({
  name,
  options = [],
  isCanAdd = false
}) => {
  const { t } = useTranslation();

  if (!isCanAdd) {
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
            options={options}
            mode="multiple"
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
                      {isCanAdd ? <Input placeholder={t('common.pleaseEnter')} /> : t('workflow.config.var-aggregator.variable')}
                    </Form.Item>
                  </Col>
                  {isCanAdd && <Col span={12} className="rb:flex! rb:items-center rb:justify-end">
                    <MinusCircleOutlined onClick={() => remove(name)} />
                  </Col>}
                </Row>

                <Form.Item
                  {...restField}
                  name={[name, 'value']}
                  noStyle
                >
                  <VariableSelect
                    placeholder={t('common.pleaseSelect')}
                    options={options}
                    mode="multiple"
                  />
                </Form.Item>
              </div>
            )
          })}
          {isCanAdd && <Form.Item noStyle>
            <Button type="dashed" onClick={() => add({ key: `Group${fields.length + 1}` })} block>
              + {t('workflow.config.var-aggregator.addGroup')}
            </Button>
          </Form.Item>}
        </>
      )}
    </Form.List>
  )
}

export default GroupVariableList