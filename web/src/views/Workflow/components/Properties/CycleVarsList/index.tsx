import { type FC } from 'react'
import { useTranslation } from 'react-i18next';
import { Form, Select, Row, Col, Input } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import VariableSelect from '../VariableSelect'

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'

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
}

const types = [
  'string',
  'number',
  'boolean',
  'array[string]',
  'array[number]',
  'array[boolean]',
  'array[object]'
]

const CycleVarsList: FC<CycleVarsListProps> = ({
  value = [],
  options,
  parentName,
  selectedNode,
  graphRef
}) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();

  // 获取循环节点的子节点变量
  const getChildNodeVariables = () => {
    if (!selectedNode || !graphRef?.current || selectedNode.getData()?.type !== 'loop') {
      return options;
    }

    const loopNodeId = selectedNode.getData()?.id;
    const childNodes = graphRef.current.getNodes().filter((node: any) => 
      node.getData()?.cycle === loopNodeId
    );

    const childVariables: Suggestion[] = [];
    childNodes.forEach((childNode: any) => {
      const childData = childNode.getData();
      if (childData?.config) {
        Object.keys(childData.config).forEach(key => {
          if (childData.config[key]?.defaultValue) {
            childVariables.push({
              key: `${childData.id}.${key}`,
              label: `${childData.name || childData.type}.${key}`,
              type: 'output',
              dataType: 'string',
              value: `${childData.id}.${key}`,
              nodeData: childData
            });
          }
        });
      }
    });

    return [...options, ...childVariables];
  };

  const availableOptions = getChildNodeVariables();

  return (
    <div>
      
      <Form.List name={parentName}>
        {(fields, { add, remove }) => (
          <>
            <div className="rb:flex rb:items-center rb:justify-between rb:mb-3">
              <span className="rb:text-sm rb:font-medium">循环变量</span>
              <PlusOutlined className="rb:text-gray-400 rb:cursor-pointer rb:hover:text-blue-500" onClick={() => add({ name: '', type: 'string', input_type: 'constant', value: '' })} />
            </div>
            {fields.map(({ key, name, ...field }, index) => {
              const currentInputType = value?.[index]?.input_type;
              
              return (
                <div key={key} className="rb:mb-3 rb:border rb:border-[#DFE4ED] rb:rounded-md rb:p-3 rb:bg-white">
                  <Row gutter={8} align="middle" className="rb:mb-2">
                    <Col span={8}>
                      <Form.Item name={[name, 'name']} noStyle>
                        <Input size="small" />
                      </Form.Item>
                    </Col>
                    <Col span={6}>
                      <Form.Item name={[name, 'type']} noStyle>
                        <Select
                          options={types.map(key => ({
                            value: key,
                            label: t(`workflow.config.parameter-extractor.${key}`),
                          }))}
                          size="small"
                          popupMatchSelectWidth={false}
                        />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item name={[name, 'input_type']} noStyle>
                        <Select
                          placeholder="Constant"
                          options={[
                            { label: 'Constant', value: 'constant' },
                            { label: 'Variable', value: 'variable' }
                          ]}
                          size="small"
                          popupMatchSelectWidth={false}
                          onChange={() => {
                            // 重置 value 字段
                            form.setFieldValue([parentName, index, 'value'], undefined);
                          }}
                        />
                      </Form.Item>
                    </Col>
                    <Col span={2}>
                      <DeleteOutlined
                        className="rb:text-gray-400 rb:cursor-pointer rb:hover:text-red-500"
                        onClick={() => remove(name)}
                      />
                    </Col>
                  </Row>
                  
                  <Form.Item name={[name, 'value']} noStyle>
                    {currentInputType === 'variable' ? (
                      <VariableSelect
                        placeholder={t('common.pleaseSelect')}
                        options={availableOptions.filter(option => {
                          const currentType = value?.[index]?.type;
                          if (!currentType) return true;

                          return option.dataType === currentType
                        })}
                      />
                    ) : (
                      <Input.TextArea
                        placeholder={t('common.pleaseEnter')}
                        rows={3}
                        className="rb:w-full"
                      />
                    )}
                  </Form.Item>
                </div>
              )
            })}
          </>
        )}
      </Form.List>
    </div>
  )
}

export default CycleVarsList