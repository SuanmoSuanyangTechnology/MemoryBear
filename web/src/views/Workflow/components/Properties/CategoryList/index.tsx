/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-09 18:34:33 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-09 18:34:33 
 */
import { type FC } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Form, Space } from 'antd';
import { Graph, Node } from '@antv/x6';

import Editor from '../../Editor';
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import { edgeAttrs, portTextAttrs, nodeWidth } from '../../../constant'

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

  // Update node ports based on category count changes (add/remove categories)
  const updateNodePorts = (caseCount: number, removedCaseIndex?: number) => {
    if (!selectedNode || !graphRef?.current) return;

    // Save existing edge connections (including left-side port connections)
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

    // Remove all existing right-side ports
    const existingPorts = selectedNode.getPorts();
    existingPorts.forEach((port: any) => {
      if (port.group === 'right') {
        selectedNode.removePort(port.id);
      }
    });

    // Calculate new node height: base height 88px + 30px for each additional port
    const baseHeight = 88;
    const newHeight = baseHeight + (caseCount - 2) * 30;

    selectedNode.prop('size', { width: nodeWidth, height: newHeight < baseHeight ? baseHeight : newHeight })

    // Add category ports
    for (let i = 0; i < caseCount; i++) {
      selectedNode.addPort({
        id: `CASE${i + 1}`,
        group: 'right',
        args: {
          x: nodeWidth,
          y: 30 * i + 42,
        },
        attrs: { text: { text: `分类${i + 1}`, ...portTextAttrs } }
      });
    }
    // Restore edge connections
    setTimeout(() => {
      edgeConnections.forEach(({ edge, sourcePortId, targetCellId, targetPortId, sourceCellId, isIncoming }: any) => {
        graphRef.current?.removeCell(edge);
        
        // If it's an incoming connection (left-side port), restore directly
        if (isIncoming) {
          const sourceCell = graphRef.current?.getCellById(sourceCellId);
          if (sourceCell) {
            graphRef.current?.addEdge({
              source: { cell: sourceCellId, port: sourcePortId },
              target: { cell: selectedNode.id, port: targetPortId },
              ...edgeAttrs
            });
          }
          return;
        }

        // Handle right-side port connections
        const originalCaseNumber = parseInt(sourcePortId.match(/CASE(\d+)/)?.[1] || '0');

        // If it's a removed port, don't recreate the connection
        if (removedCaseIndex !== undefined && originalCaseNumber === removedCaseIndex + 1) {
          return;
        }

        let newPortId = sourcePortId;

        // If a port was removed, remap subsequent port IDs
        if (removedCaseIndex !== undefined && originalCaseNumber > removedCaseIndex + 1) {
          newPortId = `CASE${originalCaseNumber - 1}`;
        }

        // Check if the new port exists
        const newPorts = selectedNode.getPorts();
        const matchingPort = newPorts.find((port: any) => port.id === newPortId);

        if (matchingPort) {
          const targetCell = graphRef.current?.getCellById(targetCellId);
          if (targetCell) {
            graphRef.current?.addEdge({
              source: { cell: selectedNode.id, port: newPortId },
              target: { cell: targetCellId, port: targetPortId },
              ...edgeAttrs
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