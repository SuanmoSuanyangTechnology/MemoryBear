import type { Graph, Node, Edge } from '@antv/x6';
import type { MutableRefObject } from 'react';
import dayjs from 'dayjs';

import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types';
import { hasErrorHandleNodes } from '../../constant';
import type { ChatVariable, EnvVariable, WorkflowConfig } from '../../types';

/**
 * Context required to serialize the graph state into a save payload.
 */
export interface BuildWorkflowSaveParamsCtx {
  graphRef: MutableRefObject<Graph | undefined>;
  config: WorkflowConfig;
  chatVariables: ChatVariable[];
  envVariables: EnvVariable[];
  featuresRef: MutableRefObject<FeaturesConfigForm | undefined>;
}

/**
 * Serialize graph state (nodes, edges, variables) into the workflow save payload.
 */
export const buildWorkflowSaveParams = ({ graphRef, config, chatVariables, envVariables, featuresRef }: BuildWorkflowSaveParamsCtx) => {
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
            let memoryMessage = { role: 'USER', content: data.config[key].defaultValue.messages }
            itemConfig = {
              ...itemConfig,
              messages: data.type === 'llm' && rest.enable ? [...itemConfig.messages, memoryMessage] : itemConfig.messages,
              memory: { ...rest },
            }
          } else if (data.config[key] && 'defaultValue' in data.config[key] && key === 'group_variables') {
            let group_variables = data.config.group.defaultValue ? {} : data.config[key].defaultValue
            if (data.config.group.defaultValue) {
              data.config[key].defaultValue.map((vo: any) => {
                group_variables[vo.key] = vo.value
              })
            }
            itemConfig[key] = group_variables
          } else if (data.config[key] && 'defaultValue' in data.config[key] && key === 'group_type') {
            let group = data.config.group.defaultValue
            let group_type = group ? {} : data.config[key].defaultValue
            let group_variables = data.config.group_variables.defaultValue

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
          } else if (data.config[key] && 'defaultValue' in data.config[key] && key !== 'knowledge_retrieval') {
            itemConfig[key] = data.config[key].defaultValue
          } else if (key === 'knowledge_retrieval' && data.config[key] && 'defaultValue' in data.config[key]) {
            const { knowledge_bases } = data.config[key].defaultValue || {}
            itemConfig = {
              ...itemConfig,
              ...(data.config[key].defaultValue || {}),
              knowledge_bases: knowledge_bases?.map((vo: any) => {
                const kb_config = vo.config || vo
                return { kb_id: vo.kb_id || vo.id, ...kb_config, }
              })
            }
          }
        })
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

  return params
}
