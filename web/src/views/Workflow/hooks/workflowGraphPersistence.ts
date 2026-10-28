import { Node, History, type Edge } from '@antv/x6';
import type { DragEvent } from 'react';
import dayjs from 'dayjs';
import { saveWorkflowConfig } from '@/api/application';
import { edgeAttrs, graphNodeLibrary, nodeLibrary, hasErrorHandleNodes } from '../constant';
import type { NodeProperties, WorkflowConfig } from '../types';
import { normalizeAgentConfig } from '../utils/normalizeAgentConfig';
import type { WorkflowGraphContext } from './types';

export const createWorkflowGraphPersistence = ({ graphRef, config, setConfig, chatVariables, setChatVariables, envVariables, setEnvVariables, featuresRef, message, t }: Pick<WorkflowGraphContext, 'graphRef' | 'config' | 'setConfig' | 'chatVariables' | 'setChatVariables' | 'envVariables' | 'setEnvVariables' | 'featuresRef' | 'message' | 't'>) => {
  const onDrop = (event: DragEvent) => {
    if (!graphRef.current) return;
    event.preventDefault();
    const dragData = JSON.parse(event.dataTransfer.getData('application/json'));
    const graph = graphRef.current;
    if (!graph) return;

    const point = graphRef.current.clientToLocal(event.clientX, event.clientY);

    // Get original config from node library to avoid config data chaining
    let nodeLibraryConfig = [...nodeLibrary]
      .flatMap(category => category.nodes)
      .find(n => n.type === dragData.type);
    nodeLibraryConfig = JSON.parse(JSON.stringify({ config: {}, ...nodeLibraryConfig })) as NodeProperties

    if (nodeLibraryConfig.type === 'trigger' && nodeLibraryConfig.config?.time) {
      nodeLibraryConfig.config.time.defaultValue = dayjs(nodeLibraryConfig.config.time.defaultValue, 'h:mm A')
    }

    // Create clean node data, only keep necessary fields
    const cleanNodeData = {
      id: `${dragData.type.replace(/-/g, '_')}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      name: t(`workflow.${dragData.type}`),
      ...nodeLibraryConfig
    };

    if (dragData.type === 'loop' || dragData.type === 'iteration') {
      graph.disableHistory()
      const parentNode = graphRef.current.addNode({
        ...graphNodeLibrary[dragData.type],
        x: point.x - 150,
        y: point.y - 100,
        id: cleanNodeData.id,
        data: { ...cleanNodeData, isGroup: true },
      })
      const parentBBox = parentNode.getBBox()
      const cycleStartId = `cycle_start_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
      const cycleStartNode = graphRef.current.addNode({
        ...graphNodeLibrary.cycleStart,
        x: parentBBox.x + 24,
        y: parentBBox.y + 70,
        id: cycleStartId,
        data: { id: cycleStartId, type: 'cycle-start', parentId: cleanNodeData.id, isDefault: true, cycle: cleanNodeData.id },
      })
      const addNode = graphRef.current.addNode({
        ...graphNodeLibrary.addStart,
        x: parentBBox.x + 24 + 84,
        y: parentBBox.y + 70 + 4,
        data: { type: 'add-node', label: t('workflow.addNode'), icon: '+', parentId: cleanNodeData.id, cycle: cleanNodeData.id },
      })
      parentNode.addChild(cycleStartNode, { silent: true })
      parentNode.addChild(addNode, { silent: true })
      const newEdge = graphRef.current.addEdge({
        source: { cell: cycleStartNode.id, port: cycleStartNode.getPorts().find(p => p.group === 'right')?.id || 'right' },
        target: { cell: addNode.id, port: addNode.getPorts().find(p => p.group === 'left')?.id || 'left' },
        ...edgeAttrs,
      })
      cycleStartNode.toFront()
      addNode.toFront()
      graph.enableHistory()
      // Manually push a single batch frame covering all 4 cells into undoStack
      const history = graph.getPlugin('history') as History
      const makeBatchCmd = (cell: any) => ({
        batch: true,
        event: 'cell:added',
        data: { id: cell.id, node: cell.isNode(), edge: cell.isEdge(), props: cell.toJSON() },
        options: {},
      })
      const batchFrame = [parentNode, cycleStartNode, addNode, newEdge].map(makeBatchCmd)
      ;(history as any).undoStack.push(batchFrame)
      ;(history as any).redoStack = []
      graph.trigger('history:change', { cmds: batchFrame, options: { name: 'add-group' } })
    } else if (dragData.type === 'if-else') {
      // Create condition node
      graphRef.current.addNode({
        ...graphNodeLibrary[dragData.type],
        x: point.x - 100,
        y: point.y - 60,
        id: cleanNodeData.id,
        data: { ...cleanNodeData },
      });
    } else {
      // Normal node creation, does not support dragging into loop node
      graphRef.current.addNode({
        ...(graphNodeLibrary[dragData.type] || graphNodeLibrary.default),
        x: point.x - 60,
        y: point.y - 20,
        id: cleanNodeData.id,
        data: { ...cleanNodeData },
      });
    }
  };
  /**
   * Save workflow configuration to backend
   * Serializes graph state (nodes, edges, variables) and sends to API
   * @param flag - Whether to show success message (default: true)
   * @returns Promise that resolves when save is complete
   */
  const handleSave = (flag = true) => {
    if (!graphRef.current || !config) return Promise.resolve()
    return new Promise((resolve, reject) => {
      const nodes = graphRef.current?.getNodes().filter((node: Node) => {
        const nodeData = node.getData();
        return nodeData?.type !== 'add-node';
      }) || [];
      const edges = graphRef.current?.getEdges() || []

      const params = {
        ...config,
        features: featuresRef.current,
        variables: chatVariables.map(v => {
          const { defaultValue, ...cleanV } = v
          return {
            ...cleanV,
            default: defaultValue ?? ''
          }
        }),
        environment_variables: envVariables,
        nodes: nodes.map((node: Node) => {
          const data = node.getData();
          const position = node.getPosition();
          let itemConfig: Record<string, any> = {}

          if (data.config) {
            Object.keys(data.config).forEach(key => {
              if (key === 'tools' && data.config[key] && 'defaultValue' in data.config[key]) {
                const tools = data.config[key].defaultValue || []
                itemConfig[key] = tools?.map((item: any) => ({
                    tool_id: item.tool_id,
                    operation: item.operation,
                    enabled: item.enabled || false
                  }))
              } else if (data.type === 'trigger' && key === 'time' && data.config[key] && 'defaultValue' in data.config[key]) {
                itemConfig[key] = dayjs(data.config[key].defaultValue, 'h:mm A').format('h:mm A')
              } else if (data.type === 'code' && key === 'code' && data.config[key] && 'defaultValue' in data.config[key]) {
                const code = data.config[key].defaultValue || ''
                itemConfig = {
                  ...itemConfig,
                  code: btoa(encodeURIComponent(code || ''))
                }
              } else if (key === 'memory' && data.config[key] && 'defaultValue' in data.config[key]) {
                const { messages, ...rest } = data.config[key].defaultValue
                const memoryMessage = { role: 'USER', content: data.config[key].defaultValue.messages }
                itemConfig = {
                  ...itemConfig,
                  messages: data.type === 'llm' && rest.enable ? [...itemConfig.messages, memoryMessage] : itemConfig.messages,
                  memory: { ...rest },
                }
              } else if (data.config[key] && 'defaultValue' in data.config[key] && key === 'group_variables') {
                const group_variables = data.config.group.defaultValue ? {} : data.config[key].defaultValue
                if (data.config.group.defaultValue) {
                  data.config[key].defaultValue.map((vo: any) => {
                    group_variables[vo.key] = vo.value
                  })
                }
                itemConfig[key] = group_variables
              } else if (data.config[key] && 'defaultValue' in data.config[key] && key === 'group_type') {
                const group = data.config.group.defaultValue
                const group_type = group ? {} : data.config[key].defaultValue
                const group_variables = data.config.group_variables.defaultValue

                if (group) {
                  group_variables.forEach((item: any, index: number) => {
                    group_type[item.key] = data.config[key].defaultValue[index] || data.config[key].defaultValue[item.key]
                  })
                }

                itemConfig[key] = group_type
              } else if (data.type === 'http-request' && (key === 'headers' || key === 'params') && data.config[key] && 'defaultValue' in data.config[key]) {
                const value = data.config[key].defaultValue
                itemConfig[key] = {}
                if (value.length > 0) {
                  value.forEach((vo: any) => {
                    itemConfig[key][vo.key] = vo.value
                  })
                }
              } else if (data.type === 'http-request' && key === 'body' && data.config[key] && 'defaultValue' in data.config[key]) {
                const value = data.config[key].defaultValue
                itemConfig[key] = value
                if (value.content_type === 'json' && value.data && value.data !== '') {
                  itemConfig[key].data = value.data.replace(/\u00a0/g, ' ')
                } else {
                  itemConfig[key].data = value.data
                }
              } else if (data.type !== 'agent' && key === 'knowledge_retrieval' && data.config[key] && 'defaultValue' in data.config[key]) {
                const { knowledge_bases } = data.config[key].defaultValue || {}
                itemConfig = {
                  ...itemConfig,
                  ...(data.config[key].defaultValue || {}),
                  knowledge_bases: knowledge_bases?.map((vo: any) => {
                    const kb_config = vo.config || vo
                    return { kb_id: vo.kb_id || vo.id, ...kb_config, }
                  })
                }
              } else if (key === 'knowledge_retrieval' && data.config[key] && 'defaultValue' in data.config[key]) {
                const { knowledge_bases, ...rest } = data.config[key].defaultValue || {}
                // itemConfig = {
                //   ...itemConfig,
                //   knowledge_retrieval: {
                //     ...rest,
                //     knowledge_bases: knowledge_bases?.map((vo: any) => {
                //       const kb_config = vo.config || vo
                //       return { kb_id: vo.kb_id || vo.id, ...kb_config, }
                //     })
                //   }
                // }
                itemConfig['knowledge_retrieval'] = {
                    ...rest,
                    knowledge_bases: knowledge_bases?.map((vo: any) => {
                      const kb_config = vo.config || vo
                      return { kb_id: vo.kb_id || vo.id, ...kb_config, }
                    })
                  }
              } else if (data.config[key] && 'defaultValue' in data.config[key]) {
                itemConfig[key] = data.config[key].defaultValue
              }
            })
          }

          if (data.type === 'agent') {
            itemConfig = normalizeAgentConfig(itemConfig)
          }

          return {
            id: data.id || node.id,
            type: data.type,
            name: data.name,
            cycle: data.cycle, // Save cycle parameter
            position: {
              x: position.x,
              y: position.y,
            },
            config: itemConfig
          };
        }),
        edges: edges.map((edge: Edge) => {
          const sourceCell = graphRef.current?.getCellById(edge.getSourceCellId());
          const targetCell = graphRef.current?.getCellById(edge.getTargetCellId());
          const sourcePortId = edge.getSourcePortId();

          // Filter invalid edges: source or target node doesn't exist, or is add-node type
          if (!sourceCell?.getData()?.id || !targetCell?.getData()?.id ||
            sourceCell?.getData()?.type === 'add-node' || targetCell?.getData()?.type === 'add-node') {
            return null;
          }

          // If if-else node right port connection, add label
          if (sourceCell?.getData()?.type === 'if-else' && sourcePortId?.startsWith('CASE')) {
            return {
              source: sourceCell.getData().id,
              target: targetCell?.getData().id,
              label: sourcePortId,
            };
          }

          // If question-classifier node right port connection, add label
          if (sourceCell?.getData()?.type === 'question-classifier' && sourcePortId?.startsWith('CASE')) {
            return {
              source: sourceCell.getData().id,
              target: targetCell?.getData().id,
              label: sourcePortId,
            };
          }
          // If human-intervention node right port connection, add label
          if (sourceCell?.getData()?.type === 'human-intervention' && (sourcePortId?.startsWith('CASE') || sourcePortId === 'TIMEOUT')) {
            return {
              source: sourceCell.getData().id,
              target: targetCell?.getData().id,
              label: sourcePortId,
            };
          }

          // If http-request node right port connection, add label
          if (hasErrorHandleNodes.includes(sourceCell?.getData()?.type)) {
            if (sourcePortId === 'ERROR') {
              return {
                source: sourceCell.getData().id,
                target: targetCell?.getData().id,
                label: 'ERROR',
              };
            } else {
              return {
                source: sourceCell.getData().id,
                target: targetCell?.getData().id,
                label: 'SUCCESS',
              };
            }
          }

          return {
            source: sourceCell?.getData().id,
            target: targetCell?.getData().id,
          };
        })
          .filter(edge => edge !== null)
          .filter((edge, index, arr) => {
            // Deduplication: For if-else and question-classifier nodes, different ports can connect to same node
            return arr.findIndex(e => {
              if (!e || !edge) return false;
              const sourceCell = graphRef.current?.getCellById(e.source);
              const sourceType = sourceCell?.getData()?.type;
              const isMultiPortNode = ['question-classifier', 'if-else', 'human-intervention'].includes(sourceType);

              if (isMultiPortNode) {
                // Multi-port nodes need to compare source, target and label
                return e.source === edge.source && e.target === edge.target && e.label === edge.label;
              } else {
                // Other nodes only compare source and target
                return e.source === edge.source && e.target === edge.target;
              }
            }) === index;
          }),
      }
      saveWorkflowConfig(config.app_id, params as WorkflowConfig)
        .then((res) => {
          if (flag) {
            message.success({ content: t('common.saveSuccess'), duration: 1 })
          }
          const { variables, environment_variables, ...rest } = res as WorkflowConfig
          const initChatVariables = variables.map(v => {
            const { default: _, ...cleanV } = v
            return {
              ...cleanV,
              defaultValue: v.default ?? ''
            }
          })
          setChatVariables(initChatVariables)
          setEnvVariables(environment_variables ?? [])
          setConfig({ ...rest, variables: initChatVariables, environment_variables: environment_variables ?? [] })
          resolve(res)
        }).catch(error => {
          reject(error)
        })
    })
  }
  return { onDrop, handleSave };
};
