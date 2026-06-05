import { type FC, useRef, useState } from "react";
import { useTranslation } from 'react-i18next'
import { Form, Space, Flex, InputNumber, Input, Button, Divider } from 'antd'
import { Graph, Node } from '@antv/x6'

import RadioGroupBtn from '../RadioGroupBtn'
import SubmitTypeList from '../SubmitTypeList'
import ButtonStyleModal, { type ButtonStyleModalRef } from './ButtonStyleModal'
import { nodeWidth, conditionNodeHeight, conditionNodeItemHeight, portItemArgsY, conditionNodePortItemArgsY, edgeAttrs } from '../../../constant'
import Editor from '../../Editor'
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'

interface HumanInterventionProps {
  options: Suggestion[];
  selectedNode?: Node | null;
  graphRef?: React.MutableRefObject<Graph | undefined>;
}

const HumanIntervention: FC<HumanInterventionProps> = ({
  selectedNode,
  graphRef,
  options
}) => {
  const { t } = useTranslation()
  const form = Form.useFormInstance();
  const buttonStyleModalRef = useRef<ButtonStyleModalRef>(null);
  const [editingField, setEditingField] = useState<{ name: number; label: string } | null>(null);

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

  const updateNodePorts = (operationCount: number, removedIndex?: number) => {
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

    // Keep left port and TIMEOUT port, only update operation ports
    const leftPorts = selectedNode.getPorts().filter((p: any) => p.group !== 'right');
    const lastRightPorts = selectedNode.getPorts().filter((p: any) => p.group === 'right');

    const newRightPorts = [
      ...Array.from({ length: operationCount }, (_, i) => ({
        id: `CASE${i + 1}`,
        group: 'right',
        args: { x: nodeWidth, y: portItemArgsY * i + conditionNodePortItemArgsY },
      })),
      {
        id: `CASE${operationCount + 1}`,
        group: 'right',
        args: {
          x: nodeWidth,
          y: conditionNodePortItemArgsY + portItemArgsY * operationCount,
        },
      },
    ];

    selectedNode.prop('ports/items', [...leftPorts, ...newRightPorts], { rewrite: true });

    // Update node height: base height + operationCount * conditionNodeItemHeight
    const newHeight = conditionNodeHeight + (operationCount - 1) * conditionNodeItemHeight;

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

      console.log('existingEdges', lastRightPorts?.length)
      // Handle TIMEOUT port (CASE1) - keep connection
      if (sourcePortId === `CASE${lastRightPorts?.length || 1}`) {
        const targetCell = graph.getCellById(targetCellId);
        if (targetCell) {
          graph.addEdge({
            source: { cell: selectedNode.id, port: `CASE${operationCount + 1}` },
            target: { cell: targetCellId, port: targetPortId },
            ...edgeAttrs
          });
          selectedNode.toFront();
          bringLoopChildrenToFront(selectedNode);
          targetCell.toFront();
          bringLoopChildrenToFront(targetCell);
        }
        return;
      }
      // Handle operation ports (CASE1, CASE2, ...)
      const originalCaseNumber = parseInt(sourcePortId.match(/CASE(\d+)/)?.[1] || '0');
      if (removedIndex !== undefined && originalCaseNumber === removedIndex + 1) return;
      let newPortId = sourcePortId;
      if (removedIndex !== undefined && originalCaseNumber > removedIndex + 1) {
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

  const handleEditType = (name: number) => {
    const actions = form.getFieldValue('actions') || [];
    const operation = actions[name];
    const currentVariant = operation?.variant || 'primary';
    const currentLabel = operation?.label || 'Button Text';
    
    setEditingField({ name, label: currentLabel });
    buttonStyleModalRef.current?.handleOpen(currentVariant, currentLabel);
  };

  const handleVariantSave = (variant: string) => {
    if (editingField !== null) {
      const actions = form.getFieldValue('actions') || [];
      actions[editingField.name] = {
        ...actions[editingField.name],
        variant
      };
      form.setFieldsValue({ actions });
      setEditingField(null);
    }
  };

  const getButtonType = (variant?: string) => {
    switch (variant) {
      case 'primary':
        return 'primary';
      case 'link':
        return 'link';
      case 'text':
        return 'text';
      default:
        return 'default';
    }
  };
  const updateFormFields = (form_fields: any[]) => {
    form.setFieldValue('form_fields', form_fields);
  };

  const formValues = Form.useWatch(['actions'], form);
  const formFields = Form.useWatch(['form_fields'], form);

  return (
    <>
      <Form.Item
        name="delivery_method"
        noStyle
      >
        <SubmitTypeList />
      </Form.Item>

      <Form.Item
        name="content"
        label={t('workflow.config.human-intervention.content')}
      >
        <Editor 
          key="url"
          options={options} 
          variant="outlined"
          size="small"
          updateFormFields={updateFormFields}
          formFields={formFields}
        />
      </Form.Item>
      <Form.Item name="form_fields" hidden />

      <Form.List name="actions">
        {(fields, { add, remove }) => (
          <>
            <Flex align="center" justify="space-between" className="rb:mb-2!">
              <div className="rb:text-[12px] rb:font-medium rb:leading-4.5">
                {t('workflow.config.human-intervention.actions')}
              </div>

              <Space size={8}>
                <Button
                  onClick={() => {
                    add({ id: `action_${fields.length + 1}`, label: `Button Text ${fields.length + 1}`, variant: 'primary' });
                    setTimeout(() => {
                      updateNodePorts(fields.length + 1);
                    }, 100);
                  }}
                  className="rb:py-0! rb:px-1! rb:h-4.5! rb:rounded-sm! rb:text-[12px]!"
                  size="small"
                >
                  +
                </Button>
              </Space>
            </Flex>
            {fields.length > 0
              ? <Flex gap={8} vertical className="rb:mb-3!">
                {fields.map(({ key, name, ...restField }) => {
                  const actions = form.getFieldValue('actions') || [];
                  const operation = actions[name];
                  const variant = operation?.variant || 'primary';
                  
                  return (
                    <Flex key={key} align="center" gap={4}>
                      <Form.Item
                        {...restField}
                        name={[name, 'id']}
                        noStyle
                      >
                        <Input 
                          placeholder={t('common.pleaseEnter')} 
                          size="small"
                          className="rb:w-27! rb:bg-[#F6F6F6]!"
                          variant="borderless"
                        />
                      </Form.Item>
                      <Form.Item
                        {...restField}
                        name={[name, 'label']}
                        noStyle
                      >
                        <Input
                          placeholder={t('common.pleaseEnter')}
                          size="small"
                          className="rb:flex-1! rb:bg-[#F6F6F6]!"
                          variant="borderless"
                        />
                      </Form.Item>
                      <Form.Item
                        {...restField}
                        name={[name, 'variant']}
                        noStyle
                      >
                        <Button 
                          type={getButtonType(variant)}
                          size="small"
                          onClick={() => handleEditType(name)}
                        >
                          Aa
                        </Button>
                      </Form.Item>
                      <div
                        className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                        onClick={() => {
                          const currentLength = (formValues?.length || 1) - 1;
                          remove(name);
                          setTimeout(() => {
                            updateNodePorts(currentLength, name);
                          }, 100);
                        }}
                      ></div>
                    </Flex>
                  );
                })}
              </Flex>
              : <Flex align="center" justify="center"
                className="rb:bg-[#F6F6F6] rb:rounded-lg rb:h-12.5 rb:mb-3! rb:text-[12px]"
              >
                {t('workflow.config.human-intervention.noActions')}
              </Flex>
            }
          </>
        )}
      </Form.List>

      <div className="rb:text-[12px] rb:font-medium rb:leading-4.5 rb:mb-2!">
        {t('workflow.config.human-intervention.timeout')}
      </div>
      <Flex gap={8} justify="space-between" align="center">
        <Form.Item layout="horizontal" name={['timeout', 'value']} noStyle>
          <InputNumber
            min={1}
            max={100}
            step={1}
            defaultValue={3}
            onBlur={(value) => {
              form.setFieldValue(['timeout', 'value'], value || 1)
            }}
            className="rb:flex-1!"
          />
        </Form.Item>
        <Form.Item name={['timeout', 'unit']} noStyle>
          <RadioGroupBtn
            options={[
              { value: 'seconds', label: t('workflow.config.human-intervention.seconds') },
              { value: 'minutes', label: t('workflow.config.human-intervention.minutes') },
              { value: 'hours', label: t('workflow.config.human-intervention.hours') },
              { value: 'days', label: t('workflow.config.human-intervention.days') },
            ]}
          />
          </Form.Item>
      </Flex>

      <Divider />

      <ButtonStyleModal
        ref={buttonStyleModalRef}
        onSave={handleVariantSave}
      />
    </>
  );
};
export default HumanIntervention;
