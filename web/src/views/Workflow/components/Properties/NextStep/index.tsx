/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-26 18:30:00 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-27 15:39:52
 */
import { type FC } from 'react';
import { useTranslation } from 'react-i18next';
import { Graph, Node } from '@antv/x6';
import { Flex, Button, type MenuProps } from 'antd';
import clsx from 'clsx';

import MoreDropdown from '@/components/MoreDropdown';

interface NextStepProps {
  selectedNode: Node;
  graphRef: React.MutableRefObject<Graph | undefined>;
  onAddNode: (e: React.MouseEvent, portId?: string) => void;
  onNodeClick: (params: { node: Node }) => void;
  nodeData: any;
}

const prevEdgeHeight = 40;
const rightGap = 4;
const targetYInit = 18;
const NextStep: FC<NextStepProps> = ({
  selectedNode,
  graphRef,
  onAddNode,
  onNodeClick,
  nodeData,
}) => {
  const { t } = useTranslation();

  const renderNextNodes = () => {
    const graph = graphRef.current;
    if (!graph) return null;

    const rightPorts = selectedNode.getPorts().filter((p: any) => p.group === 'right');

    if (!rightPorts || rightPorts.length === 0) {
      return null;
    }

    const portEdgeCounts = rightPorts.map((port) => {
      const edges = graph.getOutgoingEdges(selectedNode)?.filter((edge) => {
        return edge.getSourcePortId() === port.id;
      });
      return (edges?.length || 0);
    });

    const initHeight = nodeData.type === 'if-else' || nodeData.type === 'question-classifier' ? 64 : 42;
    const svgHeight = rightPorts.reduce((maxY, _port, index) => {
      if (index === 0) return 22;
      const prevEdgeCount = portEdgeCounts.slice(0, index).reduce((sum, count) => sum + count, 0);
      const totalHeight = initHeight * (index + 1) + prevEdgeCount * prevEdgeHeight + index * rightGap;
      const targetY = targetYInit + totalHeight;
      return Math.max(maxY, targetY + 2);
    }, 22);

    return (
      <Flex className="rb:w-full!">
        <div className="rb:w-6 rb:relative">
          <svg className="rb:w-6" viewBox={`0 0 24 ${svgHeight}`} preserveAspectRatio="none">
            <g>
              <path d="M0,18 L24,18" strokeWidth="1" fill="none" className="rb:stroke-[#EBEBEB]"></path>
              <rect x="0" y="16" width="1" height="4" className="rb:fill-[#171719]"></rect>
              <rect x="23" y="16" width="1" height="4" className="rb:fill-[#171719]"></rect>
            </g>
            {rightPorts.length > 1 && rightPorts.slice(1).map((port, i) => {
              const prevEdgeCount = portEdgeCounts.slice(0, i + 1).reduce((sum, count) => sum + count, 0);
              const totalHeight = initHeight * (i + 1) + prevEdgeCount * prevEdgeHeight + i * rightGap;
              const targetY = targetYInit + totalHeight;
              return (
                <g key={port.id}>
                  <path d={`M0,18 Q12,18 12,28 L12,${targetY - 10} Q12,${targetY} 24,${targetY}`} strokeWidth="1" fill="none" className="rb:stroke-[#EBEBEB]"></path>
                  <rect x="23" y={targetY - 2} width="1" height="4" className="rb:fill-[#171719]"></rect>
                </g>
              );
            })}
          </svg>
        </div>
        <Flex vertical gap={4} className="rb:flex-1!">
          {rightPorts.map((port, index) => {
            const portId = port.id;
            const portLabel = portId === 'ERROR'
              ? t('workflow.errorBranch')
              : nodeData.type === 'if-else' && index === rightPorts.length - 1
              ? 'ELSE'
              : nodeData.type === 'if-else'
              ? `CASE ${index}`
              : nodeData.type === 'question-classifier'
              ? `分类 ${index + 1}`
              : port.label;
            
            const outgoingEdges = graph.getOutgoingEdges(selectedNode)?.filter((edge) => {
              const sourcePort = edge.getSourcePortId();
              return sourcePort === portId;
            });

            return (
              <div key={portId}
                className={clsx("rb:w-full rb:bg-[#F6F6F6] rb:p-1 rb:rounded-md", {
                  'rb:bg-[rgba(255,93,52,0.08)]': portId === 'ERROR',
                })}
              >
                {portLabel && typeof portLabel === 'string' &&
                  <div className={clsx("rb:text-[#5B6167] rb:text-[10px] rb:font-medium rb:pl-1 rb:mb-1", {
                    'rb:text-[#FF5D34]': portId === 'ERROR',
                  })}>{portLabel}</div>
                }
                <Flex vertical gap={4}>
                  {outgoingEdges && outgoingEdges.length > 0 ? (
                    <Flex vertical gap={4}>
                      {outgoingEdges.map((edge, index) => {
                        const targetNode = edge.getTargetCell();
                        if (!targetNode || !targetNode.isNode()) return null;

                        const targetData = targetNode.getData();

                        const menuItems: MenuProps['items'] = [
                          {
                            key: 'disconnect',
                            label: t('workflow.disconnect'),
                            onClick: () => {
                              graph.removeEdge(edge);
                            }
                          },
                          {
                            key: 'delete',
                            label: t('workflow.delete'),
                            onClick: () => {
                              graph.removeNode(targetNode);
                            }
                          },
                        ];

                        return (
                          <Flex
                            key={index}
                            gap={8}
                            align="center"
                            className="rb:bg-[#FFFFFF] rb:rounded-md rb:px-2! rb:py-1.5! rb:h-9 rb:group"
                          >
                            <div className={`rb:size-4 rb:bg-cover ${targetData.icon}`} />
                            <span className="rb:text-xs rb:text-[#212332] rb:truncate rb:flex-1">
                              {targetData?.name || targetData?.label || t('workflow.unnamedNode')}
                            </span>
                            <Flex align="center" gap={8} className="rb:hidden! rb:group-hover:inline-flex!">
                              <Button size="small" className="rb:text-[12px]!"
                                onClick={() => onNodeClick({ node: targetNode })}
                              >
                                {t('workflow.jumpToNode')}
                              </Button>
                              <MoreDropdown
                                items={menuItems}
                                placement="bottomRight"
                                variant="outline"
                              />
                            </Flex>
                          </Flex>
                        );
                      })}
                      <Flex
                        align="center"
                        gap={8}
                        className="rb:rounded-md rb:px-2! rb:py-1.5! rb:border rb:border-dashed rb:border-[#DFE4ED] rb:cursor-pointer"
                        onClick={(e) => onAddNode(e, portId)}
                      >
                        <Flex align="center" justify="center" className="rb:size-5 rb:rounded-md rb-border rb:bg-[#DFE4ED] rb:text-[#5B6167]">
                          +
                        </Flex>
                        <div className="rb:text-[#5B6167]">{t('workflow.addParallelNode')}</div>
                      </Flex>
                    </Flex>
                  ) : (
                    <Flex
                      align="center"
                      gap={8}
                      className="rb:rounded-md rb:px-2! rb:py-1.5! rb:border rb:border-dashed rb:border-[#DFE4ED] rb:cursor-pointer"
                      onClick={(e) => onAddNode(e, portId)}
                    >
                      <Flex align="center" justify="center" className="rb:size-5 rb:rounded-md rb-border rb:bg-[#DFE4ED] rb:text-[#5B6167]">
                        +
                      </Flex>
                      <div className="rb:text-[#5B6167]">{t('workflow.chooseNextNode')}</div>
                    </Flex>
                  )}
                </Flex>
              </div>
            );
          })}
        </Flex>
      </Flex>
    );
  };

  if (!nodeData || nodeData.type === 'output') return null;

  return (
    <div className="rb:text-[12px] rb:leading-4.5 rb-border-t rb:mt-3 rb:px-3 rb:pt-3">
      <div className="rb:font-medium">
        {t('workflow.nextStep')}
      </div>
      <div className="rb:text-[#5B6167] rb:mt-1">{t('workflow.nextStepTip')}</div>

      <div className="rb:mt-3">
        <Flex gap={0} align="start" className="rb:w-full!">
          <div className="rb:relative">
            <Flex align="center" justify="center" className="rb:size-7 rb:rounded-md rb-border">
              <div className={`rb:size-4 rb:bg-cover ${nodeData.icon}`} />
            </Flex>
          </div>
          <div className="rb:flex-1">
            {renderNextNodes()}
          </div>
        </Flex>
      </div>
    </div>
  );
};

export default NextStep;
