/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-09 18:34:33 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-05 18:18:35
 */
import { type FC } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Form, Flex } from 'antd';
import { Graph, Node } from '@antv/x6';

import Editor from '../../Editor';
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import { edgeAttrs, conditionNodeItemHeight, nodeWidth, portItemArgsY, conditionNodePortItemArgsY, conditionNodeHeight } from '../../../constant'

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

  const bringLoopChildrenToFront = (cell: any) => {
    const type = cell?.getData()?.type;
    if ((type !== 'loop' && type !== 'iteration') || !graphRef?.current) return;
    const cycleId = cell.getData().id;
    graphRef.current.getEdges().forEach((edge: any) => {
      const src = graphRef.current?.getCellById(edge.getSourceCellId());
      const tgt = graphRef.current?.getCellById(edge.getTargetCellId());
      if (src?.getData()?.cycle === cycleId || tgt?.getData()?.cycle === cycleId) edge.toFront();
    });
    graphRef.current.getNodes().forEach((n: any) => {
      if (n.getData()?.cycle === cycleId) n.toFront();
    });
  };

  // Update node ports based on category count changes (add/remove categories)
  const updateNodePorts = (caseCount: number, removedCaseIndex?: number) => {
    if (!selectedNode || !graphRef?.current) return;
    const graph = graphRef.current;

    const existingEdges = graph.getEdges().filter((edge: any) =>
      edge.getSourceCellId() === selectedNode.id || edge.getTargetCellId() === selectedNode.id
    );
    const edgeConnections = existingEdges.map((edge: any) => ({
      sourcePortId: edge.getSourcePortId(),
      targetCellId: edge.getTargetCellId(),
      targetPortId: edge.getTargetPortId(),
      sourceCellId: edge.getSourceCellId(),
      isIncoming: edge.getTargetCellId() === selectedNode.id,
    }));

    graph.startBatch('update-ports');

    existingEdges.forEach((edge: any) => graph.removeCell(edge));
    // Replace all ports in one prop call — produces a single cell:change:ports command
    const leftPorts = selectedNode.getPorts().filter((p: any) => p.group !== 'right');
    const newRightPorts = Array.from({ length: caseCount }, (_, i) => ({
      id: `CASE${i + 1}`,
      group: 'right',
      args: { x: nodeWidth, y: portItemArgsY * i + conditionNodePortItemArgsY },
    }));
    selectedNode.prop('ports/items', [...leftPorts, ...newRightPorts], { rewrite: true });

    const newHeight = conditionNodeHeight + (caseCount - 2) * conditionNodeItemHeight;
    selectedNode.prop('size', { width: nodeWidth, height: newHeight < conditionNodeHeight ? conditionNodeHeight : newHeight });

    edgeConnections.forEach(({ sourcePortId, targetCellId, targetPortId, sourceCellId, isIncoming }: any) => {
      if (isIncoming) {
        const sourceCell = graph.getCellById(sourceCellId);
        if (sourceCell) {
          graph.addEdge({
            source: { cell: sourceCellId, port: sourcePortId },
            target: { cell: selectedNode.id, port: targetPortId },
            ...edgeAttrs
          });
          sourceCell.toFront();
          bringLoopChildrenToFront(sourceCell);
          selectedNode.toFront();
          bringLoopChildrenToFront(selectedNode);
        }
        return;
      }
      const originalCaseNumber = parseInt(sourcePortId.match(/CASE(\d+)/)?.[1] || '0');
      if (removedCaseIndex !== undefined && originalCaseNumber === removedCaseIndex + 1) return;
      let newPortId = sourcePortId;
      if (removedCaseIndex !== undefined && originalCaseNumber > removedCaseIndex + 1) {
        newPortId = `CASE${originalCaseNumber - 1}`;
      }
      if (newRightPorts.find((p) => p.id === newPortId)) {
        const targetCell = graph.getCellById(targetCellId);
        if (targetCell) {
          graph.addEdge({
            source: { cell: selectedNode.id, port: newPortId },
            target: { cell: targetCellId, port: targetPortId },
            ...edgeAttrs
          });
          selectedNode.toFront();
          bringLoopChildrenToFront(selectedNode);
          targetCell.toFront();
          bringLoopChildrenToFront(targetCell);
        }
      }
    });

    graph.stopBatch('update-ports');
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
        <Flex gap={8} vertical>
          {fields.map(({ key, name, ...restField }, index) => {
            const currentItem = formValues?.[key] || {};
            const contentLength = (currentItem.class_name || '').length;
            
            return (
              <div key={key} className="rb-border rb:rounded-md rb:p-2">
                <Flex align="center" justify="space-between" className="rb:mb-2!">
                  <div className="rb:text-[12px] rb:font-medium rb:py-1 rb:leading-4">{t('workflow.config.question-classifier.class_name')} {index + 1}</div>
                  <Flex align="center" gap={4}>
                    <span className="rb:text-xs rb:text-[#667085]">{contentLength}</span>
                    <div
                      className="rb:ml-1 rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                      onClick={() => handleRemoveCategory(remove, name, index)}
                    ></div>
                  </Flex>
                </Flex>
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
        </Flex>
      )}
    </Form.List>
  );
};

export default CategoryList;