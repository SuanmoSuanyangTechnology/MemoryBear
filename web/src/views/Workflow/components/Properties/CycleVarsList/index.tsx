import { type FC } from 'react'
import { useTranslation } from 'react-i18next';
import { Form, Select, Input, Button, InputNumber } from 'antd'
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
  size?: 'small' | 'middle'
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
  graphRef,
  size = 'middle'
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
    <Form.List name={parentName}>
      {(fields, { add, remove }) => (
        <>
          <div className="rb:flex rb:items-center rb:justify-between rb:mb-3">
            <span className="rb:text-[12px] rb:font-medium">{t('workflow.config.loop.cycle_vars')}</span>
            <Button
              onClick={() => add({ name: '', type: 'string', input_type: 'constant', value: '' })}
              className="rb:py-0! rb:px-1! rb:text-[12px]!"
              size="small"
            >
              + {t('workflow.config.addVariable')}
            </Button>
          </div>
          {fields.map(({ key, name }, index) => {
            const currentType = value?.[index]?.type;
            const currentInputType = value?.[index]?.input_type;
            
            return (
              <div key={key} className="rb:flex rb:items-start rb:mb-2">
                <div className="rb:flex-1 rb:bg-[#F6F8FC] rb:border rb:border-[#DFE4ED] rb:rounded-md">
                  <div className="rb:flex rb:gap-1 rb:p-1 rb:border-b rb:border-b-[#DFE4ED]">
                    <Form.Item name={[name, 'name']} noStyle>
                      <Input size={size} className="rb:w-23!" placeholder={t('common.pleaseEnter')} />
                    </Form.Item>
                    <Form.Item name={[name, 'type']} noStyle>
                      <Select
                        options={types.map(key => ({
                          value: key,
                          label: t(`workflow.config.parameter-extractor.${key}`),
                        }))}
                        size={size}
                        popupMatchSelectWidth={false}
                        className="rb:w-18.5!"
                      />
                    </Form.Item>
                    <Form.Item name={[name, 'input_type']} noStyle>
                      <Select
                        placeholder="Constant"
                        options={[
                          { label: 'Constant', value: 'constant' },
                          { label: 'Variable', value: 'variable' }
                        ]}
                        size={size}
                        popupMatchSelectWidth={false}
                        onChange={() => {
                          form.setFieldValue([parentName, index, 'value'], undefined);
                        }}
                        className="rb:w-18!"
                      />
                    </Form.Item>
                  </div>
                  
                  <Form.Item name={[name, 'value']} noStyle>
                    {currentInputType === 'variable'
                      ? (
                      <VariableSelect
                        placeholder={t('common.pleaseSelect')}
                        options={availableOptions.filter(option => {
                          const currentType = value?.[index]?.type;
                          if (!currentType) return true;

                          return option.dataType === currentType
                        })}
                        variant="borderless"
                        size="small"
                      />
                    )
                    : currentType === 'number'
                    ? <InputNumber
                      placeholder={t('common.pleaseEnter')}
                      variant="borderless"
                      className="rb:w-full! rb:my-1!"
                      onChange={(value) => form.setFieldValue([name, 'value'], value)}
                    />
                    : (
                      <Input.TextArea
                        placeholder={t('common.pleaseEnter')}
                        rows={3}
                        className="rb:w-full"
                        variant="borderless"
                      />
                    )}
                  </Form.Item>
                </div>
                <div
                  className="rb:ml-1 rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                  onClick={() => remove(name)}
                ></div>
              </div>
            )
          })}
        </>
      )}
    </Form.List>
  )
}

export default CycleVarsList