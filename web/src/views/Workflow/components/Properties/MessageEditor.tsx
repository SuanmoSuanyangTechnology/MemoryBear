import { type FC, useMemo } from 'react';
import { useTranslation } from 'react-i18next'
import { Input, Form, Space, Button, Row, Col, Select, type FormListOperation } from 'antd';
import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons';
import { Graph, Node } from '@antv/x6';
import Editor from '../Editor'
import type { Suggestion } from '../Editor/plugin/AutocompletePlugin'

interface TextareaProps {
  isArray?: boolean;
  parentName?: string;
  label?: string;
  placeholder?: string;
  value?: string;
  onChange?: (value?: string) => void;
  selectedNode?: Node | null;
  graphRef?: React.MutableRefObject<Graph | undefined>;
}
const roleOptions = [
  // { label: 'SYSTEM', value: 'SYSTEM' },
  { label: 'USER', value: 'USER' },
  { label: 'ASSISTANT', value: 'ASSISTANT' },
]
const MessageEditor: FC<TextareaProps> = ({
  isArray = true,
  parentName = 'messages',
  placeholder,
  selectedNode,
  graphRef,
}) => {
  const { t } = useTranslation()
  const form = Form.useFormInstance();
  const values = form.getFieldsValue()
  
  const suggestions = useMemo(() => {
    if (!selectedNode || !graphRef?.current) return [];
    
    const suggestions: Suggestion[] = [];
    const graph = graphRef.current;
    const edges = graph.getEdges();
    const nodes = graph.getNodes();
    
    // Find all connected previous nodes (recursive)
    const getAllPreviousNodes = (nodeId: string, visited = new Set<string>()): string[] => {
      if (visited.has(nodeId)) return [];
      visited.add(nodeId);
      
      const directPrevious = edges
        .filter(edge => edge.getTargetCellId() === nodeId)
        .map(edge => edge.getSourceCellId());
      
      const allPrevious = [...directPrevious];
      directPrevious.forEach(prevNodeId => {
        allPrevious.push(...getAllPreviousNodes(prevNodeId, visited));
      });
      
      return allPrevious;
    };
    
    const allPreviousNodeIds = getAllPreviousNodes(selectedNode.id);
    console.log('allPreviousNodeIds', allPreviousNodeIds)
    
    allPreviousNodeIds.forEach(nodeId => {
      const node = nodes.find(n => n.id === nodeId);
      if (!node) return;
      
      const nodeData = node.getData();

      switch(nodeData.type) {
        case 'start':
          const list = [
            ...(nodeData.config?.variables?.defaultValue ?? []),
            ...(nodeData.config?.variables?.value ?? [])
          ]
          list.forEach((variable: any) => {
            suggestions.push({
              key: `${nodeId}_${variable.name}`,
              label: variable.name,
              type: 'variable',
              dataType: variable.type,
              value: `${nodeId}.${variable.name}`,
              nodeData: nodeData,
            });
          });
          nodeData.config?.variables?.sys.forEach((variable: any) => {
            suggestions.push({
              key: `${nodeId}_${variable.name}`,
              label: `sys.${variable.name}`,
              type: 'variable',
              dataType: variable.type,
              value: `sys.${variable.name}`,
              nodeData: nodeData,
            });
          });
          break
        case 'llm':
          suggestions.push({
            key: `${nodeId}_output`,
            label: 'output',
            type: 'variable',
            dataType: 'String',
            value: `${nodeId}.output`,
            nodeData: nodeData,
          });
          break
      }
    });

    return suggestions;
  }, [selectedNode, graphRef]);

  const handleAdd = (add: FormListOperation['add']) => {
    const list = values[parentName];
    const lastRole = list[list.length - 1].role

    add({
      role: lastRole === 'USER' ? 'ASSISTANT' : 'USER',
      content: undefined
    })
  }

  return (
    <div>
      {isArray
        ? <Form.List name={parentName}>
          {(fields, { add, remove }) => (
            <Space size={12} direction="vertical" className="rb:w-full">
              {fields.map(({ key, name, ...restField }) => {
                const currentRole = values[parentName]?.[key].role || 'USER'
                
                return (
                  <Space key={key} size={12} direction="vertical" className="rb:w-full rb:border rb:border-[#DFE4ED] rb:rounded-md rb:px-2 rb:py-1.5 rb:bg-white">
                    <Row>
                      <Col span={12}>
                        <Form.Item
                          {...restField}
                          name={[name, 'role']}
                          noStyle
                        >
                          {currentRole === 'SYSTEM'
                            ? <Input disabled />
                            :
                            <Select
                              options={roleOptions}
                              disabled={currentRole === 'SYSTEM'}
                            />
                          }
                        </Form.Item>
                      </Col>
                      {currentRole !== 'SYSTEM' && <Col span={12}>
                        <div className="rb:h-full rb:flex rb:justify-end rb:items-center">
                          <MinusCircleOutlined onClick={() => remove(name)} />
                        </div>
                      </Col>}
                    </Row>
                    <Form.Item
                      {...restField}
                      name={[name, 'content']}
                      noStyle
                    >
                      <Editor placeholder={placeholder} suggestions={suggestions} />
                    </Form.Item>
                  </Space>
                )
              })}
              <Form.Item>
                <Button type="dashed" onClick={() => handleAdd(add)} block icon={<PlusOutlined />}>
                  +{t('workflow.addMessage')}
                </Button>
              </Form.Item>
            </Space >
          )}
        </Form.List>
        :
        <Space size={12} direction="vertical" className="rb:w-full rb:border rb:border-[#DFE4ED] rb:rounded-md rb:px-2 rb:py-1.5 rb:bg-white">
          <Row>
            <Col span={12}>
              {t('workflow.answerDesc')}
            </Col>
          </Row>
          <Form.Item
            name={parentName}
            noStyle
          >
            <Editor placeholder={placeholder} suggestions={suggestions} />
          </Form.Item>
        </Space>
        }
    </div>
  );
};

export default MessageEditor;