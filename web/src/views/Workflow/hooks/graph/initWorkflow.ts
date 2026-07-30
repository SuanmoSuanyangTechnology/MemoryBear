import type { Graph, Node } from '@antv/x6';
import type { PortMetadata } from '@antv/x6/lib/model/port';
import type { MutableRefObject } from 'react';
import type { TFunction } from 'i18next';
import dayjs from 'dayjs';

import { conditionNodeHeight, conditionNodeItemHeight, conditionNodePortItemArgsY, defaultAbsolutePortGroups, defaultPortItems, edgeAttrs, graphNodeLibrary, nodeLibrary, nodeWidth, notesConfig, portAttrs, portItemArgsY, portMarkup, portTextAttrs, unknownNode, hasErrorHandleNodes } from '../../constant';
import type { ChatVariable, EnvVariable, NodeProperties, WorkflowConfig } from '../../types';
import { calcConditionNodeTotalHeight, getConditionNodeCasePortY } from '../../utils';
import { reorderCells } from './reorderCells';
import { isSafari } from './env';

/**
 * Context required to initialize the workflow graph from configuration.
 */
export interface InitWorkflowCtx {
  graphRef: MutableRefObject<Graph | undefined>;
  config: WorkflowConfig | null;
  chatVariables: ChatVariable[];
  envVariables: EnvVariable[];
  t: TFunction;
}

/**
 * Initialize workflow graph with nodes and edges from configuration
 */
export const initWorkflow = ({ graphRef, config, chatVariables, envVariables, t }: InitWorkflowCtx) => {
  if (!config || !graphRef.current) return
  const { nodes, edges } = config

  if (nodes.length) {
    const nodeList = nodes.map(node => {
      const { id, type, name, position, config = {} } = node
      let nodeLibraryConfig: NodeProperties | undefined = [...nodeLibrary, { nodes: [unknownNode, notesConfig] }]
        .flatMap(category => category.nodes)
        .find(n => n.type === type) as NodeProperties || unknownNode
      nodeLibraryConfig = JSON.parse(JSON.stringify({ ...nodeLibraryConfig, config: nodeLibraryConfig.config || {} }))

      if (nodeLibraryConfig?.config) {
        Object.keys(nodeLibraryConfig.config).forEach(key => {
          if (type === 'trigger' && key === 'time' && nodeLibraryConfig.config) {
            nodeLibraryConfig.config[key].defaultValue = dayjs('12:00 AM', 'h:mm A')
          } else if (type === 'loop' && key === 'condition' && nodeLibraryConfig.config) {
            const { condition } = config;
            nodeLibraryConfig.config[key].defaultValue = condition ? {
              ...condition,
              expressions: (condition as any).expressions.map((expr: any) => {
                return expr.input_type ? { ...expr, input_type: expr.input_type.toLocaleLowerCase() } : expr
              })
            } : {}
          } else if (type === 'if-else' && key === 'cases' && nodeLibraryConfig.config) {
            const { cases } = config;
            nodeLibraryConfig.config[key].defaultValue = cases && Array.isArray(cases) ? cases.map(item => ({
              ...item,
              expressions: item.expressions.map((expr: any) => {
                return expr.input_type ? { ...expr, input_type: expr.input_type.toLocaleLowerCase() } : expr
              }),
            })) : []
          } else if (type === 'memory-write' && key === 'message' && nodeLibraryConfig.config) {
            nodeLibraryConfig.config['messages'].defaultValue = [{ role: 'USER', content: config[key] }]
            delete nodeLibraryConfig.config[key]
          } else if (key === 'memory' && nodeLibraryConfig.config && nodeLibraryConfig.config[key] && type === 'llm') {
            const { memory, messages } = config as any;
            if (memory?.enable && messages && messages.length > 0) {
              const lastMessage = messages[messages.length - 1]
              nodeLibraryConfig.config[key].defaultValue = {
                ...memory,
                messages: lastMessage.content
              }
              nodeLibraryConfig.config.messages.defaultValue.splice(-1, 1)
            }
          } else if (key === 'knowledge_retrieval' && nodeLibraryConfig.config && nodeLibraryConfig.config[key]) {
            const { query, ...rest } = config
            nodeLibraryConfig.config[key].defaultValue = {
              ...rest
            }
          } else if (key === 'group_variables' && nodeLibraryConfig.config && nodeLibraryConfig.config[key]) {
            const { group_variables, group } = config
            nodeLibraryConfig.config[key].defaultValue = group
              ? Object.entries(group_variables as Record<string, any>).map(([key, value]) => ({ key, value }))
              : group_variables
          } else if (type === 'http-request' && (key === 'headers' || key === 'params') && config[key] && typeof config[key] === 'object' && !Array.isArray(config[key]) && nodeLibraryConfig.config && nodeLibraryConfig.config[key]) {
            nodeLibraryConfig.config[key].defaultValue = Object.entries(config[key]).map(([key, value]) => ({ key, value }))
          } else if (type === 'code' && key === 'code' && config[key] && nodeLibraryConfig.config && nodeLibraryConfig.config[key]) {
            try {
              nodeLibraryConfig.config[key].defaultValue = decodeURIComponent(atob(config[key] as string))
            } catch {
              nodeLibraryConfig.config[key].defaultValue = config[key]
            }
          } else if (nodeLibraryConfig.config && nodeLibraryConfig.config[key] && config[key]) {
            nodeLibraryConfig.config[key].defaultValue = config[key]
          }
        })
      }

      const nodeConfig = {
        ...(graphNodeLibrary[type] ?? graphNodeLibrary.default),
        id,
        type,
        name,
        data: { ...node, ...nodeLibraryConfig, ...((['if-else', 'question-classifier', 'human-intervention'].includes(type)) ? { chatVariables, envVariables } : {}) },
        ...position,
      }

      if (type === 'start' && config?.variables && Array.isArray(config.variables)) {
        config?.variables?.forEach(item => {
          item.ui_type = item.ui_type || (item.type === 'string' ? 'text-input' : item.type === 'number' ? 'number' : 'boolean')
        })
      }

      if (type === 'notes') {
        const w = config.width;
        const h = config.height;
        if (w) nodeConfig.width = w as number;
        if (h) nodeConfig.height = h as number;
      }

      // Generate ports dynamically for if-else node based on cases
      if (type === 'if-else' && config.cases && Array.isArray(config.cases)) {
        const totalPorts = config.cases.length + 1; // IF/ELIF + ELSE

        const portItems: PortMetadata[] = [
          defaultPortItems[0],
        ];
        // Add IF/ELIF/ELSE ports
        for (let i = 0; i < totalPorts; i++) {
          portItems.push({
            group: 'right',
            id: `CASE${i + 1}`,
            args: {
              x: nodeWidth,
              y: getConditionNodeCasePortY(config.cases, i),
            },
          });
        }

        nodeConfig.ports = {
          groups: defaultAbsolutePortGroups,
          items: portItems
        };

        nodeConfig.height = calcConditionNodeTotalHeight(config.cases);
      }

      // Generate ports dynamically for question-classifier node based on categories
      if (type === 'question-classifier' && config.categories && Array.isArray(config.categories)) {
        const categoryCount = config.categories.length;
        const newHeight = conditionNodeHeight + (categoryCount - 2) * conditionNodeItemHeight;

        const portItems: PortMetadata[] = [
          defaultPortItems[0]
        ];

        // Add category ports
        config.categories.forEach((_category: any, index: number) => {
          portItems.push({
            group: 'right',
            id: `CASE${index + 1}`,
            args: {
              x: nodeWidth,
              y: portItemArgsY * index + conditionNodePortItemArgsY,
            },
          });
        });

        nodeConfig.ports = {
          groups: defaultAbsolutePortGroups,
          items: portItems
        };

        nodeConfig.height = newHeight;
      }
      // Check error_handle.method config for http-request node
      if (hasErrorHandleNodes.includes(type) && (config as any)?.error_handle?.method === 'branch') {
        nodeConfig.ports = {
          groups: {
            right: { position: 'right', markup: portMarkup, attrs: portAttrs },
            left: { position: 'left', markup: portMarkup, attrs: portAttrs },
          },
          items: [
            defaultPortItems[0],
            { ...defaultPortItems[1], id: 'right' },
            {
              ...defaultPortItems[1],
              args: {
                x: nodeWidth,
                y: portItemArgsY + portItemArgsY,
              },
              id: 'ERROR', attrs: { text: { text: t('workflow.config.http-request.errorBranch'), ...portTextAttrs } }
            }
          ]
        };
      }
      // Generate ports dynamically for human-intervention node based on actions
      if (type === 'human-intervention' && config.actions && Array.isArray(config.actions)) {
        const actionCount = config.actions.length;
        const newHeight = conditionNodeHeight + (actionCount - 1) * conditionNodeItemHeight;

        const portItems: PortMetadata[] = [
          defaultPortItems[0]
        ];

        // Add action ports
        config.actions.forEach((_action: any, index: number) => {
          portItems.push({
            group: 'right',
            id: `CASE${index + 1}`,
            args: {
              x: nodeWidth,
              y: portItemArgsY * index + conditionNodePortItemArgsY,
            },
          });
        });
        portItems.push({
          group: 'right',
          id: `TIMEOUT`,
          args: {
            x: nodeWidth,
            y: portItemArgsY * actionCount + conditionNodePortItemArgsY,
          },
        });

        nodeConfig.ports = {
          groups: defaultAbsolutePortGroups,
          items: portItems
        };

        nodeConfig.height = newHeight;
      }

      return nodeConfig
    })

    // Separate parent nodes and child nodes
    const parentNodes = nodeList.filter(node => !node.data.cycle)
    const childNodes = nodeList.filter(node => node.data.cycle)

    // Add parent nodes first
    graphRef.current?.addNodes(parentNodes)

    // Then process child nodes, use addChild to add to corresponding parent node
    childNodes.forEach(childNode => {
      const cycleId = childNode.data.cycle
      if (cycleId) {
        const parentNode = graphRef.current?.getCellById(cycleId)
        if (parentNode) {
          const addedChild = graphRef.current?.addNode(childNode)
          if (addedChild) {
            parentNode.addChild(addedChild, { silent: true })
          }
        }
      }
    })

    // Adjust parent node size to fit child nodes
    setTimeout(() => {
      const parentNodesWithChildren = parentNodes.filter(parentNode => {
        const parentId = parentNode.data.id
        return childNodes.some(child => child.data.cycle === parentId)
      })

      parentNodesWithChildren.forEach(parentNodeConfig => {
        const parentNode = graphRef.current?.getCellById(parentNodeConfig.data.id)
        if (parentNode) {
          const children = parentNode.getChildren()
          if (children && children.length > 0) {
            const childBounds = children.map(child => child.getBBox())
            const minX = Math.min(...childBounds.map(b => b.x))
            const minY = Math.min(...childBounds.map(b => b.y))
            const maxX = Math.max(...childBounds.map(b => b.x + b.width))
            const maxY = Math.max(...childBounds.map(b => b.y + b.height))

            const padding = 24
            const headerHeight = 50
            const parentBBox = parentNode.getBBox()

            const newWidth = Math.max(parentBBox.width, maxX - minX + padding * 2)
            const newHeight = Math.max(parentBBox.height, maxY - minY + padding * 2 + headerHeight)

            parentNode.prop('size', { width: newWidth, height: newHeight })

            // Update x position of right group ports
            const ports = (parentNode as Node).getPorts()
            ports.forEach(port => {
              if (port.group === 'right' && port.args) {
                (parentNode as Node).portProp(port.id!, 'args/x', newWidth)
              }
            })
          }
        }
      })
    }, 100)
  }
  if (edges.length) {
    // Deduplication: For if-else and question-classifier nodes, different ports can connect to same node
    const uniqueEdges = edges.filter((edge, index, arr) => {
      return arr.findIndex(e => {
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
    });

    const edgeList = uniqueEdges.map(edge => {
      const { source, target, label } = edge
      const sourceCell = graphRef.current?.getCellById(source)
      const targetCell = graphRef.current?.getCellById(target)

      if (sourceCell && targetCell) {
        const sourcePorts = (sourceCell as Node).getPorts()
        const targetPorts = (targetCell as Node).getPorts()

        let sourcePort = sourcePorts.find((port: any) => port.group === 'right')?.id || 'right';

        // If if-else node has label, match corresponding port by label
        if (sourceCell.getData()?.type === 'if-else' && label) {
          // Find matching port ID
          const matchingPort = sourcePorts.find((port: any) => port.id === label);
          if (matchingPort) {
            sourcePort = label;
          }
        }

        // If question-classifier node has label, match corresponding port by label
        if (sourceCell.getData()?.type === 'question-classifier' && label) {
          const matchingPort = sourcePorts.find((port: any) => port.id === label);
          if (matchingPort) {
            sourcePort = label;
          }
        }
        // If human-intervention node has label, match corresponding port by label
        if (sourceCell.getData()?.type === 'human-intervention' && label) {
          // Find matching port ID
          const matchingPort = sourcePorts.find((port: any) => port.id === label);
          if (matchingPort) {
            sourcePort = label;
          }
        }

        // If http-request node has label, match corresponding port by label
        if (hasErrorHandleNodes.includes(sourceCell.getData()?.type) && label) {
          const matchingPort = sourcePorts.find((port: any) => port.id === label);
          if (matchingPort) {
            sourcePort = label;
          }
        }

        const edgeConfig = {
          source: {
            cell: sourceCell.id,
            port: sourcePort
          },
          target: {
            cell: targetCell.id,
            port: targetPorts.find((port: any) => port.group === 'left')?.id || 'left'
          },
          connector: { name: 'smooth' },
          ...edgeAttrs
          // zIndex: loopIterationCount
        }

        return edgeConfig
      }
      return null
    })
    graphRef.current.addEdges(edgeList.filter(vo => vo !== null))
  }

  // Check if loop/iteration nodes need add-node added
  const parentNodes = graphRef.current.getNodes().filter(node => {
    const type = node.getData()?.type;
    return type === 'loop' || type === 'iteration';
  });

  parentNodes.forEach(parentNode => {
    const parentData = parentNode.getData();
    const allChildren = graphRef.current!.getNodes().filter(n => n.getData()?.cycle === parentData.id);
    const cycleStartNodes = allChildren.filter(n => n.getData()?.type === 'cycle-start');

    // If only cycle-start exists, add add-node
    if (cycleStartNodes.length === 1 && allChildren.length === 1) {
      const cycleStartNode = cycleStartNodes[0];
      const bbox = cycleStartNode.getBBox();
      const addNode = graphRef.current!.addNode({
        ...graphNodeLibrary.addStart,
        x: bbox.x + 84,
        y: bbox.y + 4,
        data: { type: 'add-node', parentId: parentNode.id, cycle: parentData.id, label: t('workflow.addNode'), icon: '+' },
      });
      parentNode.addChild(addNode, { silent: true });
      graphRef.current!.addEdge({
        source: { cell: cycleStartNode.id, port: cycleStartNode.getPorts().find(p => p.group === 'right')?.id || 'right' },
        target: { cell: addNode.id, port: addNode.getPorts().find(p => p.group === 'left')?.id || 'left' },
        ...edgeAttrs,
      });
    }
  });

  graphRef.current.centerContent()
  // Initialize after completion, display nodes in visible area
  if (nodes.length > 0 || edges.length > 0) {
    setTimeout(() => {
      if (graphRef.current) {
        if (isSafari) {
          reorderCells(graphRef.current)
        } else {
          graphRef.current.getNodes().forEach(node => {
            if (!node.getData()?.cycle) node.toFront();
          });
          // Bring edges to front first, then child nodes above edges; parent nodes stay behind
          graphRef.current.getEdges().forEach(edge => {
            const sourceCell = graphRef.current?.getCellById(edge.getSourceCellId());
            const targetCell = graphRef.current?.getCellById(edge.getTargetCellId());
            if (sourceCell?.getData()?.cycle || targetCell?.getData()?.cycle) {
              edge.toFront();
            }
          });
          graphRef.current.getNodes().forEach(node => {
            if (node.getData()?.cycle) node.toFront();
          });
        }
        graphRef.current.enableHistory()
        graphRef.current.cleanHistory()
      }
    }, isSafari ? 0 : 200)
  } else {
    graphRef.current.enableHistory()
    graphRef.current.cleanHistory()
  }
}
