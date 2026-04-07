/*
 * @Author: ZhaoYing 
 * @Date: 2026-01-19 17:00:26 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-07 20:33:26
 */
/**
 * useVariableList Hook
 * 
 * This hook provides functionality for managing and retrieving variables in workflow nodes.
 * It handles variable extraction from different node types, including:
 * - Node-specific output variables
 * - Chat variables
 * - Loop and iteration variables
 * - Connected node variables
 */
import { useMemo, useEffect, useState } from 'react';
import { Graph, Node } from '@antv/x6';
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin';
import type { ChatVariable } from '../../../types';

export const fileSubVariable = [
  { label: 'type', dataType: 'string', filed: 'type' },
  { label: 'size', dataType: 'number', filed: 'size' },
  { label: 'name', dataType: 'string', filed: 'name' },
  { label: 'url', dataType: 'string', filed: 'url' },
  { label: 'extension', dataType: 'string', filed: 'extension' },
  { label: 'mime_type', dataType: 'string', filed: 'mime_type' },
  { label: 'related_id', dataType: 'string', filed: 'related_id' },
];

/**
 * Node variable definitions
 * 
 * Maps node types to their available output variables
 */
const NODE_VARIABLES = {
  llm: [{ label: 'output', dataType: 'string', field: 'output' }],
  'jinja-render': [{ label: 'output', dataType: 'string', field: 'output' }],
  tool: [{ label: 'data', dataType: 'string', field: 'data' }],
  'knowledge-retrieval': [{ label: 'output', dataType: 'array[object]', field: 'output' }],
  'parameter-extractor': [
    { label: '__is_success', dataType: 'number', field: '__is_success' },
    { label: '__reason', dataType: 'string', field: '__reason' }
  ],
  'http-request': [
    { label: 'body', dataType: 'string', field: 'body' },
    { label: 'status_code', dataType: 'number', field: 'status_code' },
    { label: 'headers', dataType: 'object', field: 'headers' },
  ],
  'question-classifier': [{ label: 'class_name', dataType: 'string', field: 'class_name' }],
  'memory-read': [
    { label: 'answer', dataType: 'string', field: 'answer' },
    { label: 'intermediate_outputs', dataType: 'array[object]', field: 'intermediate_outputs' }
  ],
  'document-extractor': [
    { label: 'text', dataType: 'string', field: 'text' },
  ],
  'list-operator': [
    { label: 'result', dataType: 'array[string]', field: 'result' },
    { label: 'first_record', dataType: 'string', field: 'first_record' },
    { label: 'last_record', dataType: 'string', field: 'last_record' },
  ] // dataType will be overridden dynamically
} as const;

/**
 * Add variable to list if not already present
 * 
 * @param {Suggestion[]} list - List of suggestions to add to
 * @param {Set<string>} keys - Set of existing keys to check for duplicates
 * @param {string} key - Unique key for the variable
 * @param {string} label - Human-readable label for the variable
 * @param {string} dataType - Data type of the variable
 * @param {string} value - Variable value/expression
 * @param {any} nodeData - Node data associated with the variable
 * @param {Partial<Suggestion>} [extra] - Additional suggestion properties
 */
const buildFileChildren = (key: string, value: string, nodeData: any, parentLabel: string): Suggestion[] =>
  fileSubVariable.map(sub => ({
    key: `${key}_${sub.filed}`,
    label: sub.label,
    type: 'variable',
    dataType: sub.dataType,
    value: `${value}.${sub.filed}`,
    nodeData,
    parentLabel,
  }));

const addVariable = (
  list: Suggestion[],
  keys: Set<string>,
  key: string,
  label: string,
  dataType: string,
  value: string,
  nodeData: any,
  extra?: Partial<Suggestion>
) => {
  if (!keys.has(key)) {
    keys.add(key);
    const children = dataType === 'file'
      ? buildFileChildren(key, value, nodeData, label)
      : undefined;
    list.push({ key, label, type: 'variable', dataType, value, nodeData, children, ...extra });
  }
};

/**
 * Process node variables based on node type
 * 
 * @param {any} nodeData - Node data object
 * @param {string} dataNodeId - Node ID
 * @param {Suggestion[]} variableList - List to add variables to
 * @param {Set<string>} addedKeys - Set of already added keys
 */
const processNodeVariables = (
  nodeData: any,
  dataNodeId: string,
  variableList: Suggestion[],
  addedKeys: Set<string>
) => {
  const { type, config } = nodeData;

  // Add node-specific variables
  if (type in NODE_VARIABLES) {
    if (type === 'list-operator') {
      // Determine output type from the first variable in config
      const variableValue = config?.input_list?.defaultValue;
      let itemType = 'string';
      if (variableValue) {
        const refVar = variableList.find(v => `{{${v.value}}}` === variableValue);
        if (refVar?.dataType.startsWith('array[')) {
          itemType = refVar.dataType.replace(/^array\[(.+)\]$/, '$1');
        } else if (refVar) {
          itemType = refVar.dataType;
        }
      }
      addVariable(variableList, addedKeys, `${dataNodeId}_result`, 'result', `array[${itemType}]`, `${dataNodeId}.result`, nodeData);
      addVariable(variableList, addedKeys, `${dataNodeId}_first_record`, 'first_record', itemType, `${dataNodeId}.first_record`, nodeData);
      addVariable(variableList, addedKeys, `${dataNodeId}_last_record`, 'last_record', itemType, `${dataNodeId}.last_record`, nodeData);
    } else {
      NODE_VARIABLES[type as keyof typeof NODE_VARIABLES].forEach(({ label, dataType, field }) => {
        addVariable(variableList, addedKeys, `${dataNodeId}_${label}`, label, dataType, `${dataNodeId}.${field}`, nodeData);
      });
    }
  }

  // Process special node types
  switch (type) {
    case 'start':
      // Add start node variables
      [...(config?.variables?.defaultValue ?? []), ...(config?.variables?.value ?? [])].forEach((v: any) => {
        if (v?.name) addVariable(variableList, addedKeys, `${dataNodeId}_${v.name}`, v.name, v.type, `${dataNodeId}.${v.name}`, nodeData);
      });
      // Add system variables
      config?.variables?.sys?.forEach((v: any) => {
        if (v?.name) addVariable(variableList, addedKeys, `${dataNodeId}_sys_${v.name}`, `sys.${v.name}`, v.type, `sys.${v.name}`, nodeData);
      });
      break;

    case 'parameter-extractor':
      // Add extracted parameters
      (config?.params?.defaultValue || []).forEach((p: any) => {
        if (p?.name) addVariable(variableList, addedKeys, `${dataNodeId}_${p.name}`, p.name, p.type || 'string', `${dataNodeId}.${p.name}`, nodeData);
      });
      break;
    
    case 'var-aggregator':
      // Add aggregated variables
      if (config.group.defaultValue) {
        (config.group_variables.defaultValue || []).forEach((gv: any) => {
          if (gv?.key) {
            let dt = 'string';
            if (gv.value?.[0]) {
              const fv = variableList.find(v => `{{${v.value}}}` === gv.value[0]);
              if (fv) dt = fv.dataType;
            }
            addVariable(variableList, addedKeys, `${dataNodeId}_${gv.key}`, gv.key, dt, `${dataNodeId}.${gv.key}`, nodeData);
          }
        });
      } else {
        const fv = (config.group_variables.defaultValue || [])[0];
        let dt = 'any';
        if (fv) {
          const found = variableList.find(v => `{{${v.value}}}` === fv);
          if (found) dt = found.dataType;
        }
        addVariable(variableList, addedKeys, `${dataNodeId}_output`, 'output', dt, `${dataNodeId}.output`, nodeData);
      }
      break;

    case 'iteration':
      // Add iteration output variable
      let dt = 'string';
      if (nodeData.output) {
        const sv = variableList.find(v => v.value === nodeData.output);
        if (sv) dt = sv.dataType;
      }
      addVariable(variableList, addedKeys, `${dataNodeId}_output`, 'output', `array[${dt}]`, `${dataNodeId}.output`, nodeData);
      break;

    case 'loop':
      // Add loop cycle variables
      (config.cycle_vars.defaultValue || []).forEach((cv: any) => {
        if (cv.name?.trim()) addVariable(variableList, addedKeys, `${dataNodeId}_cycle_${cv.name}`, cv.name, cv.type || 'string', `${dataNodeId}.${cv.name}`, nodeData);
      });
      break;
      
    case 'code':
      // Add code node output variables
      (config.output_variables.defaultValue || []).forEach((cv: any) => {
        if (cv.name?.trim()) addVariable(variableList, addedKeys, `${dataNodeId}_cycle_${cv.name}`, cv.name, cv.type || 'string', `${dataNodeId}.${cv.name}`, nodeData);
      });
      break;
  }
};

/**
 * Node types that have output variables
 */
const hasOutputNodeTypes = [
  'llm',
  'knowledge-retrieval',
  'memory-read',
  'question-classifier',
  'var-aggregator',
  'http-request',
  'tool',
  'jinja-render',
  'document-extractor',
  'list-operator'
];

/**
 * Get variables for the current node
 * 
 * @param {any} nodeData - Node data object
 * @param {any} values - Additional values to merge with node config
 * @returns {Suggestion[]} List of node variables
 */
export const getCurrentNodeVariables = (nodeData: any, values: any, upstreamVariables: Suggestion[] = []): Suggestion[] => {
  if (!nodeData || !hasOutputNodeTypes.includes(nodeData.type)) return [];
  const list: Suggestion[] = [...upstreamVariables];
  const keys = new Set<string>(upstreamVariables.map(v => v.key));
  const dataNodeId = nodeData.id;

  processNodeVariables({
    ...nodeData,
    config: {
      ...nodeData.config,
      ...values
    }
  }, dataNodeId, list, keys);
  
  // Special case: var-aggregator without group enabled returns no variables
  const result = list.filter(v => v.nodeData?.id === dataNodeId);
  return nodeData.type === 'var-aggregator' && !nodeData.config.group.defaultValue ? [] : result;
};

/**
 * Get variables from child nodes in a loop/iteration
 * 
 * @param {Node} selectedNode - Selected node
 * @param {React.MutableRefObject<Graph | undefined>} graphRef - Graph reference
 * @returns {Suggestion[]} List of child node variables
 */
export const getChildNodeVariables = (
  selectedNode: Node,
  graphRef: React.MutableRefObject<Graph | undefined>
): Suggestion[] => {
  const graph = graphRef.current;
  if (!graph) return [];

  const list: Suggestion[] = [];
  const nodes = graph.getNodes();
  const edges = graph.getEdges();
  const keys = new Set<string>();

  // Find child nodes in the same cycle
  const childNodes = nodes.filter(node => node.getData()?.cycle === selectedNode.id);

  /**
   * Get all connected nodes recursively
   * @param {string} nodeId - Node ID to start from
   * @param {Set<string>} visited - Set of visited node IDs
   * @returns {string[]} List of connected node IDs
   */
  const getConnectedNodes = (nodeId: string, visited = new Set<string>()): string[] => {
    if (visited.has(nodeId)) return [];
    visited.add(nodeId);
    const prev = edges.filter(e => e.getTargetCellId() === nodeId).map(e => e.getSourceCellId());
    return [...prev, ...prev.flatMap(id => getConnectedNodes(id, visited))];
  };

  // Collect all relevant node IDs
  const relevantIds = new Set<string>();
  childNodes.forEach(child => {
    relevantIds.add(child.id);
    getConnectedNodes(child.id).forEach(id => relevantIds.add(id));
  });

  // Process each relevant node
  relevantIds.forEach(id => {
    const node = nodes.find(n => n.id === id);
    if (!node) return;

    const nodeData = node.getData();
    const nodeId = nodeData.id;
    const { type } = nodeData;

    // Add node-specific variables
    if (type in NODE_VARIABLES) {
      NODE_VARIABLES[type as keyof typeof NODE_VARIABLES].forEach(({ label, dataType, field }) => {
        addVariable(list, keys, `${nodeId}_${label}`, label, dataType, `${nodeId}.${field}`, nodeData);
      });
    }

    // Add parameter-extractor variables
    if (type === 'parameter-extractor') {
      (nodeData.config?.params?.defaultValue || []).forEach((p: any) => {
        if (p?.name) addVariable(list, keys, `${nodeId}_${p.name}`, p.name, p.type || 'string', `${nodeId}.${p.name}`, nodeData);
      });
    }
    // Add code node variables
    if (type === 'code') {
      (nodeData.config?.output_variables?.defaultValue || []).forEach((p: any) => {
        if (p?.name) addVariable(list, keys, `${nodeId}_${p.name}`, p.name, p.type || 'string', `${nodeId}.${p.name}`, nodeData);
      });
    }
  });

  return list;
};

/**
 * Hook for managing workflow variable list
 * 
 * @param {Node | null | undefined} selectedNode - Currently selected node
 * @param {React.MutableRefObject<Graph | undefined>} graphRef - Graph reference
 * @param {ChatVariable[]} chatVariables - List of chat variables
 * @returns {Suggestion[]} List of available variables
 */
export const useVariableList = (
  selectedNode: Node | null | undefined,
  graphRef: React.MutableRefObject<Graph | undefined>,
  chatVariables: ChatVariable[]
) => {
  const [trigger, setTrigger] = useState(0);

  const variableList = useMemo(() => {
    if (!selectedNode || !graphRef?.current) return [];

    const list: Suggestion[] = [];
    const graph = graphRef.current;
    const edges = graph.getEdges();
    const nodes = graph.getNodes();
    const keys = new Set<string>();

    /**
     * Get all previous connected nodes recursively
     * @param {string} nodeId - Node ID to start from
     * @param {Set<string>} visited - Set of visited node IDs
     * @returns {string[]} List of previous node IDs
     */
    const getPreviousNodes = (nodeId: string, visited = new Set<string>()): string[] => {
      if (visited.has(nodeId)) return [];
      visited.add(nodeId);
      const prev = edges.filter(e => e.getTargetCellId() === nodeId).map(e => e.getSourceCellId());
      return [...prev, ...prev.flatMap(id => getPreviousNodes(id, visited))];
    };

    /**
     * Get parent loop/iteration node
     * @param {string} nodeId - Node ID to check
     * @returns {Node | null} Parent loop/iteration node or null
     */
    const getParentLoop = (nodeId: string): Node | null => {
      const node = nodes.find(n => n.id === nodeId);
      const cycle = node?.getData()?.cycle;
      if (cycle) {
        const parent = nodes.find(n => n.getData().id === cycle);
        if (parent?.getData()?.type === 'loop' || parent?.getData()?.type === 'iteration') return parent;
      }
      return null;
    };

    // Collect relevant node IDs
    const childIds = nodes.filter(n => n.getData()?.cycle === selectedNode.id).map(n => n.id);
    const parentLoop = getParentLoop(selectedNode.id);
    const relevantIds = [...getPreviousNodes(selectedNode.id), ...childIds, ...(parentLoop ? getPreviousNodes(parentLoop.id) : [])];

    // Add chat variables
    chatVariables?.forEach(v => addVariable(list, keys, `CONVERSATION_${v.name}`, v.name, v.type, `conv.${v.name}`, { type: 'CONVERSATION', name: 'CONVERSATION', icon: '' }, { group: 'CONVERSATION' }));

    // Process each relevant node: non-list-operator first, then list-operator
    const listOperatorIds: string[] = [];
    relevantIds.forEach(id => {
      const node = nodes.find(n => n.id === id);
      if (!node) return;
      if (node.getData()?.type === 'list-operator') {
        listOperatorIds.push(id);
      } else {
        processNodeVariables(node.getData(), node.getData().id, list, keys);
      }
    });
    listOperatorIds.forEach(id => {
      const node = nodes.find(n => n.id === id);
      if (node) processNodeVariables(node.getData(), node.getData().id, list, keys);
    });

    // Add parent loop variables
    if (parentLoop) {
      const pd = parentLoop.getData();
      const pid = pd.id;
      if (pd.type === 'loop') {
        (pd.cycle_vars || []).forEach((cv: any) => addVariable(list, keys, `${pid}_cycle_${cv.name}`, cv.name, cv.type || 'String', `${pid}.${cv.name}`, pd));
      } else if (pd.type === 'iteration' && pd.config.input.defaultValue) {
        let itemType = 'object';
        const iv = list.find(v => `{{${v.value}}}` === pd.config.input.defaultValue);
        if (iv?.dataType.startsWith('array[')) {itemType = iv.dataType.replace(/^array\[(.+)\]$/, '$1');}
        addVariable(list, keys, `${pid}_item`, 'item', itemType, `${pid}.item`, pd);
        addVariable(list, keys, `${pid}_index`, 'index', 'number', `${pid}.index`, pd);
      } else if (pd.type === 'iteration' && !pd.config.input.defaultValue) {
        addVariable(list, keys, `${pid}_item`, 'item', 'string', `${pid}.item`, pd);
        addVariable(list, keys, `${pid}_index`, 'index', 'number', `${pid}.index`, pd);
      }
    }

    return list;
  }, [selectedNode, graphRef, trigger, chatVariables]);

  // Refresh variable list when graph changes
  useEffect(() => {
    if (!graphRef?.current) return;
    const graph = graphRef.current;
    const handler = () => setTrigger(p => p + 1);
    const events = ['edge:added', 'edge:removed', 'edge:changed', 'edge:connected', 'node:added', 'node:removed', 'node:change:data'];
    events.forEach(e => graph.on(e, handler));
    return () => events.forEach(e => graph.off(e, handler));
  }, [graphRef]);

  return variableList;
};
