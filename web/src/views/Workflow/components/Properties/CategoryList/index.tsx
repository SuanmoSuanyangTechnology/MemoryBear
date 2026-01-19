import { type FC } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Form, Space } from 'antd';
import { Graph, Node } from '@antv/x6';

import Editor from '../../Editor';
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'

interface CategoryListProps {
  parentName: string;
  options: Suggestion[];
  selectedNode?: Node | null;
  graphRef?: React.MutableRefObject<Graph | undefined>;
}

const CategoryList: FC<CategoryListProps> = ({ parentName, selectedNode, graphRef, options }) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();
  const formValues = Form.useWatch([parentName], form);

  const updateNodePorts = (caseCount: number, removedCaseIndex?: number) => {
    if (!selectedNode || !graphRef?.current) return;

    // 保存现有连线信息（包括左侧端口连线）
    const existingEdges = graphRef.current.getEdges().filter((edge: any) =>
      edge.getSourceCellId() === selectedNode.id || edge.getTargetCellId() === selectedNode.id
    );
    const edgeConnections = existingEdges.map((edge: any) => ({
      edge,
      sourcePortId: edge.getSourcePortId(),
      targetCellId: edge.getTargetCellId(),
      targetPortId: edge.getTargetPortId(),
      sourceCellId: edge.getSourceCellId(),
      isIncoming: edge.getTargetCellId() === selectedNode.id
    }));

    // 移除所有现有的右侧端口
    const existingPorts = selectedNode.getPorts();
    existingPorts.forEach((port: any) => {
      if (port.group === 'right') {
        selectedNode.removePort(port.id);
      }
    });

    // 计算新的节点高度：基础高度88px + 每个额外port增加30px
    const baseHeight = 88;
    const totalPorts = caseCount + 1; // IF/ELIF + ELSE
    const newHeight = baseHeight + (totalPorts - 2) * 30;

    selectedNode.prop('size', { width: 240, height: newHeight < baseHeight ? baseHeight : newHeight })

    // 添加 分类 端口
    for (let i = 0; i < caseCount; i++) {
      selectedNode.addPort({
        id: `CASE${i + 1}`,
        group: 'right',
        args: i === 0 ? { dy: 24 } : undefined,
        attrs: { text: { text: `分类${i + 1}`, fontSize: 12, fill: '#5B6167' } }
      });
    }
    // 恢复连线
    setTimeout(() => {
      edgeConnections.forEach(({ edge, sourcePortId, targetCellId, targetPortId, sourceCellId, isIncoming }: any) => {
        graphRef.current?.removeCell(edge);
        
        // 如果是进入连线（左侧端口），直接恢复
        if (isIncoming) {
          const sourceCell = graphRef.current?.getCellById(sourceCellId);
          if (sourceCell) {
            graphRef.current?.addEdge({
              source: { cell: sourceCellId, port: sourcePortId },
              target: { cell: selectedNode.id, port: targetPortId },
              attrs: {
                line: {
                  stroke: '#155EEF',
                  strokeWidth: 1,
                  targetMarker: {
                    name: 'block',
                    size: 8,
                  },
                },
              },
            });
          }
          return;
        }

        // 处理右侧端口连线
        const originalCaseNumber = parseInt(sourcePortId.match(/CASE(\d+)/)?.[1] || '0');

        // 如果是被删除的端口，不重新创建连线
        if (removedCaseIndex !== undefined && originalCaseNumber === removedCaseIndex + 1) {
          return;
        }

        let newPortId = sourcePortId;

        // 如果删除了某个端口，需要重新映射后续端口的ID
        if (removedCaseIndex !== undefined && originalCaseNumber > removedCaseIndex + 1) {
          newPortId = `CASE${originalCaseNumber - 1}`;
        }

        // 检查新端口是否存在
        const newPorts = selectedNode.getPorts();
        const matchingPort = newPorts.find((port: any) => port.id === newPortId);

        if (matchingPort) {
          const targetCell = graphRef.current?.getCellById(targetCellId);
          if (targetCell) {
            graphRef.current?.addEdge({
              source: { cell: selectedNode.id, port: newPortId },
              target: { cell: targetCellId, port: targetPortId },
              attrs: {
                line: {
                  stroke: '#155EEF',
                  strokeWidth: 1,
                  targetMarker: {
                    name: 'block',
                    size: 8,
                  },
                },
              },
            });
          }
        }
      });
    }, 50);
  };

  const handleAddCategory = (addFunc: Function) => {
    addFunc({});
    setTimeout(() => {
      updateNodePorts((formValues?.length || 0) + 1);
    }, 100);
  };

  const handleRemoveCategory = (removeFunc: Function, fieldName: number, categoryIndex: number) => {
    removeFunc(fieldName);
    setTimeout(() => {
      updateNodePorts((formValues?.length || 1) - 1, categoryIndex);
    }, 100);
  };

  console.log('formValues', formValues)
  return (
    <Form.List name={parentName}>
      {(fields, { add, remove }) => (
        <Space direction="vertical" size={12} className="rb:w-full">
          {fields.map(({ key, name, ...restField }, index) => {
            const currentItem = formValues?.[key] || {};
            const contentLength = (currentItem.class_name || '').length;
            
            return (
            <div key={key} className="rb:border rb:border-[#DFE4ED] rb:rounded-md rb:p-2 rb:bg-[#F8F9FB]">
              <div className="rb:flex rb:items-center rb:justify-between rb:mb-2">
                <div className="rb:text-[12px] rb:font-medium rb:py-1 rb:leading-2">{t('workflow.config.question-classifier.class_name')} {index + 1}</div>
                <div className="rb:flex rb:items-center rb:gap-1">
                  <span className="rb:text-xs rb:text-gray-500">{contentLength}</span>
                  <div
                    className="rb:ml-1 rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                    onClick={() => handleRemoveCategory(remove, name, index)}
                  ></div>
                </div>
              </div>
              <Form.Item
                {...restField}
                  name={[name, 'class_name']}
                noStyle
              >
                <Editor
                  placeholder={t('common.pleaseEnter')}
                  options={options}
                  size="small"
                />
              </Form.Item>
            </div>
          )})}
          
          <Button
            type="dashed"
            size="middle"
            block
            onClick={() => handleAddCategory(add)}
            className="rb:text-[12px]!"
          >
            + {t('workflow.config.question-classifier.addClassName')}
          </Button>
        </Space>
      )}
    </Form.List>
  );
};

export default CategoryList;