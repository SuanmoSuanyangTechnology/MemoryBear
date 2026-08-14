/*
 * @Author: ZhaoYing 
 * @Date: 2026-01-19 17:00:26 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-08-13 17:25:29
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
import type { ChatVariable, EnvVariable } from '../../../types';

export const sysVariable = [
  { name: "message", type: "string",
    readonly: true
  },
  {
    name: "conversation_id",
    type: "string",
    readonly: true
  },
  {
    name: "execution_id",
    type: "string",
    readonly: true
  },
  {
    name: "workspace_id",
    type: "string",
    readonly: true
  },
  {
    name: "user_id",
    type: "string",
    readonly: true
  },
  {
    name: "files",
    type: "array[file]",
    readonly: true
  },
]
export const fileSubVariable = [
  { label: 'type', dataType: 'string', filed: 'type' },
  { label: 'size', dataType: 'number', filed: 'size' },
  { label: 'name', dataType: 'string', filed: 'name' },
  { label: 'url', dataType: 'string', filed: 'url' },
  { label: 'extension', dataType: 'string', filed: 'extension' },
  { label: 'mime_type', dataType: 'string', filed: 'mime_type' },
  { label: 'origin_file_type', dataType: 'string', filed: 'origin_file_type' },
  { label: 'file_id', dataType: 'string', filed: 'file_id' },
];

/**
 * Node variable definitions
 * 
 * Maps node types to their available output variables
 */
const NODE_VARIABLES = {
  llm: [
    { label: 'output', dataType: 'string', field: 'output' },
    { label: 'reasoning_content', dataType: 'string', field: 'reasoning_content' },
    { label: 'history', dataType: 'array[object]', field: 'history' },
  ],
  'jinja-render': [{ label: 'output', dataType: 'string', field: 'output' }],
  tool: [{ label: 'data', dataType: 'string', field: 'data' }],
  'knowledge-retrieval': [{ label: 'output', dataType: 'array[string]', field: 'output' }],
  'parameter-extractor': [
    { label: '__is_success', dataType: 'number', field: '__is_success' },
    { label: '__reason', dataType: 'string', field: '__reason' }
  ],
  'human-intervention': [
    { label: '__action_id', dataType: 'string', field: '__action_id' },
    { label: '__rendered_content', dataType: 'string', field: '__rendered_content' },
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
    // { label: 'chunks', dataType: 'array[string]', field: 'chunks' },
    { label: 'images', dataType: 'array[file]', field: 'images' },
  ],
  'list-operator': [
    { label: 'result', dataType: 'array[string]', field: 'result' },
    { label: 'first_record', dataType: 'string', field: 'first_record' },
    { label: 'last_record', dataType: 'string', field: 'last_record' },
  ],
  'agent': [
    { label: 'output', dataType: 'string', field: 'output' },
    { label: 'usage', dataType: 'object', field: 'usage' },
    { label: 'files', dataType: 'array[file]', field: 'files' },
    { label: 'json', dataType: 'array[object]', field: 'json' },
  ]
} as const;

export const triggerParams: Record<string, string> = {
  query_params: 'query',
  header_params: 'headers',
  req_body_params: 'body',
}

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
const buildFileChildren = (key: string, value: string, nodeData: any, parentLabel: string): Suggestion[] =>{
  return fileSubVariable.map(sub => ({
    key: `${key}_${sub.filed}`,
    label: sub.label,
    type: 'variable',
    dataType: sub.dataType,
    value: `${value}.${sub.filed}`,
    nodeData,
    parentLabel,
  }))
};

/**
 * Unwrap a config value that may be stored directly or wrapped in
 * { defaultValue: T } or { value: T } format.
 */
const unwrapConfigValue = <T,>(val: any, fallback: T): T => {
  if (val === null || val === undefined) return fallback;
  if (typeof val === 'object' && !Array.isArray(val)) {
    if ('defaultValue' in val) return val.defaultValue ?? fallback;
    if ('value' in val) return val.value ?? fallback;
  }
  return val as T;
};
const unwrapConfigArray = <T,>(val: any): T[] => {
  if (Array.isArray(val)) return val;
  return unwrapConfigValue<T[]>(val, []);
};
const unwrapConfigPrimitive = (val: any): any => {
  if (typeof val !== 'object' || val === null || Array.isArray(val)) return val;
  if ('defaultValue' in val) return val.defaultValue;
  if ('value' in val) return val.value;
  return val;
};

/**
 * Remove all variables that belong to the given nodeId from the list and
 * the key set. This is used before (re-)processing a deferred node so a
 * previous "default/fallback" type pass can be replaced with the correct
 * inferred type once upstream dependencies are available.
 */
const removeVariablesForNode = (
  list: Suggestion[],
  keys: Set<string>,
  nodeId: string
) => {
  for (let i = list.length - 1; i >= 0; i--) {
    if (list[i].nodeData?.id === nodeId) {
      keys.delete(list[i].key);
      list.splice(i, 1);
    }
  }
};

/**
 * For dependency-based node types (list-operator, var-aggregator, iteration)
 * determine whether the node successfully resolved its input reference to a
 * variable already present in the variableList.
 *
 * When false, the multi-pass processor should keep this node in the
 * "remaining" queue and retry it on the next pass (after its dependency has
 * likely been processed).
 */
const deferredNodeResolved = (nodeData: any, variableList: Suggestion[]): boolean => {
  const { type, config } = nodeData;
  if (type === 'list-operator') {
    const variableValue = unwrapConfigPrimitive(config?.input_list);
    if (!variableValue) return false;
    return variableList.some(v => `{{${v.value}}}` === variableValue);
  }
  if (type === 'var-aggregator') {
    const groupEnabled = unwrapConfigPrimitive(config?.group);
    const groupVariables = unwrapConfigArray<any>(config?.group_variables);
    if (groupEnabled) {
      // Consider resolved once at least one entry references a known variable
      // OR no entries at all (empty group).
      if (groupVariables.length === 0) return true;
      return groupVariables.some((gv: any) => {
        const gvValue = unwrapConfigArray<any>(gv.value);
        if (!gvValue?.[0]) return false;
        return variableList.some(v => `{{${v.value}}}` === gvValue[0]);
      });
    }
    const fv = groupVariables[0];
    if (!fv) return true;
    return variableList.some(v => `{{${v.value}}}` === fv);
  }
  if (type === 'iteration') {
    const output = unwrapConfigPrimitive(nodeData.output ?? config?.output);
    if (!output) return false;
    return variableList.some(v => v.value === output);
  }
  return true;
};

const addVariable = (
  list: Suggestion[],
  keys: Set<string>,
  key: string,
  label: string,
  dataType: string,
  value: string,
  nodeData: any,
  extra?: Partial<Suggestion>,
  defaultValue?: any
) => {
  if (!keys.has(key)) {
    keys.add(key);
    const children = dataType === 'file'
      ? buildFileChildren(key, value, nodeData, label)
      : undefined;
    list.push({ key, label, type: 'variable', dataType, value, nodeData, children, default: defaultValue, ...extra });
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
      // Determine output type from the input list variable reference
      const variableValue = unwrapConfigPrimitive(config?.input_list);
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
      // Add start node variables (supports direct array OR wrapped in defaultValue/value)
      unwrapConfigArray<any>(config?.variables).forEach((v: any) => {
        if (v?.name) {
          addVariable(
            variableList,
            addedKeys,
            `${dataNodeId}_${v.name}`,
            v.name,
            v.type,
            `${dataNodeId}.${v.name}`,
            nodeData,
            {
              ui_type: v.ui_type,
              options: v.options,
            },
            unwrapConfigPrimitive(v.defaultValue ?? v.default),
          );
        }
      });
      break;

    case 'parameter-extractor':
      // Add extracted parameters
      unwrapConfigArray<any>(config?.params).forEach((p: any) => {
        if (p?.name) addVariable(variableList, addedKeys, `${dataNodeId}_${p.name}`, p.name, p.type || 'string', `${dataNodeId}.${p.name}`, nodeData, undefined, unwrapConfigPrimitive(p.defaultValue ?? p.default));
      });
      break;
    
    case 'var-aggregator': {
      // Add aggregated variables
      const groupEnabled = unwrapConfigPrimitive(config?.group);
      const groupVariables = unwrapConfigArray<any>(config?.group_variables);
      if (groupEnabled) {
        groupVariables.forEach((gv: any) => {
          if (gv?.key) {
            let dt = 'string';
            const gvValue = unwrapConfigArray<any>(gv.value);
            if (gvValue?.[0]) {
              const fv = variableList.find(v => `{{${v.value}}}` === gvValue[0]);
              if (fv) dt = fv.dataType;
            }
            addVariable(variableList, addedKeys, `${dataNodeId}_${gv.key}`, gv.key, dt, `${dataNodeId}.${gv.key}`, nodeData, undefined, unwrapConfigPrimitive(gv.defaultValue ?? gv.default));
          }
        });
      } else {
        const fv = groupVariables[0];
        let dt = 'any';
        if (fv) {
          const found = variableList.find(v => `{{${v.value}}}` === fv);
          if (found) dt = found.dataType;
        }
        addVariable(variableList, addedKeys, `${dataNodeId}_output`, 'output', dt, `${dataNodeId}.output`, nodeData);
      }
      break;
    }

    case 'iteration': {
      // The output may reference a child variable as a raw path or a {{...}} expression.
      const output = unwrapConfigPrimitive(nodeData.output ?? config?.output);
      const sourceVariable = variableList.find(v =>
        v.value === output || `{{${v.value}}}` === output
      );
      const outputType = sourceVariable?.dataType ?? 'string';
      addVariable(variableList, addedKeys, `${dataNodeId}_output`, 'output', `array[${outputType}]`, `${dataNodeId}.output`, nodeData);
      break;
    }

    case 'loop':
      // Add loop cycle variables
      (config.cycle_vars?.defaultValue || []).forEach((cv: any) => {
        if (cv.name?.trim()) addVariable(variableList, addedKeys, `${dataNodeId}_cycle_${cv.name}`, cv.name, cv.type || 'string', `${dataNodeId}.${cv.name}`, nodeData, undefined, cv.defaultValue ?? cv.default);
      });
      break;
      
    case 'code':
      // Add code node output variables
      (config.output_variables?.defaultValue || []).forEach((cv: any) => {
        if (cv.name?.trim()) addVariable(variableList, addedKeys, `${dataNodeId}_cycle_${cv.name}`, cv.name, cv.type || 'string', `${dataNodeId}.${cv.name}`, nodeData, undefined, cv.defaultValue ?? cv.default);
      });
      break;
    case 'llm':
      // Add structured output variables when structured_output is enabled
      const structuredOutput = config.structured_output?.defaultValue ?? config.structured_output;
      if (structuredOutput) {
        const jsonOutputFields = config.json_output_fields?.defaultValue ?? config.json_output_fields ?? [];

        // Build children variables recursively, recursing all the way down
        // for any field that has nested children
        const buildChildren = (fields: any[], parentPath: string = ''): Suggestion[] => {
          const children: Suggestion[] = [];
          fields.forEach((field: any) => {
            if (!field.name) return;
            const fieldPath = parentPath ? `${parentPath}.${field.name}` : field.name;
            const fieldKey = `${dataNodeId}_structured_output_${fieldPath.replace(/\./g, '_')}`;
            const child: Suggestion = {
              key: fieldKey,
              label: field.name,
              type: 'variable',
              dataType: field.type,
              value: `${dataNodeId}.structured_output.${fieldPath}`,
              nodeData,
            };
            // Recursively build children for any field with nested children,
            // recursing all the way down until no more children exist
            if (field.children?.length) {
              child.children = buildChildren(field.children, fieldPath);
            }
            children.push(child);
          });
          return children;
        };
        
        const children = buildChildren(jsonOutputFields);
        
        // Add parent structured_output variable with children
        addVariable(variableList, addedKeys, `${dataNodeId}_structured_output`, 'structured_output', 'object', `${dataNodeId}.structured_output`, nodeData, { children });
      }
      break;

    case 'trigger':
      // Add webhook trigger variables
      const triggerType = config.trigger_type?.defaultValue ?? config.trigger_type;
      if (triggerType === 'webhook') {
        Object.keys(triggerParams).forEach((key: any) => {
          const configParams = Array.isArray(config[key]?.defaultValue)
            ? config[key]?.defaultValue
            : Array.isArray(config[key])
              ? config[key]
              : [];
          configParams.forEach((param: any) => {
            if (param?.name) addVariable(variableList, addedKeys, `${dataNodeId}_${param.name}`, `${key}.${param.name}`, param.type || 'string', `${dataNodeId}.${key}.${param.name}`, nodeData, undefined);
          });
        });
        addVariable(variableList, addedKeys, `${dataNodeId}_webhook_raw`, 'webhook_raw', 'object', `${dataNodeId}.webhook_raw`, nodeData, undefined);
      }
      break;
    case 'human-intervention':
      // Add human intervention form fields as variables
      (config.form_fields?.defaultValue || []).forEach((field: any) => {
        if (field.id?.trim()) {
          addVariable(variableList, addedKeys, `${dataNodeId}_${field.id}`, field.id, 'string', `${dataNodeId}.${field.id}`, nodeData, undefined, field.default_value);
        }
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
  'list-operator',
  'trigger',
  'human-intervention',
  'agent'
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

  // Merge config with values, handling defaultValue format correctly
  const mergedConfig = { ...nodeData.config };
  Object.keys(values || {}).forEach(key => {
    // If the original config has defaultValue format, preserve it
    if (mergedConfig[key]?.defaultValue !== undefined) {
      mergedConfig[key] = { defaultValue: values[key] };
    } else {
      mergedConfig[key] = values[key];
    }
  });

  processNodeVariables({
    ...nodeData,
    config: mergedConfig
  }, dataNodeId, list, keys);
  
  // Special case: var-aggregator without group enabled returns no variables
  const result = list.filter(v => v.nodeData?.id === dataNodeId);
  const groupEnabled = unwrapConfigPrimitive(nodeData.config?.group);
  return nodeData.type === 'var-aggregator' && !groupEnabled ? [] : result;
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

  // Process each relevant node: deferred types last with multi-pass to resolve chains
  const deferredIds: string[] = [];
  relevantIds.forEach(id => {
    const node = nodes.find(n => n.id === id);
    if (!node) return;
    const t = node.getData()?.type;
    if (['var-aggregator', 'list-operator', 'iteration'].includes(t)) {
      deferredIds.push(id);
    } else {
      processNodeVariables(node.getData(), node.getData().id, list, keys);
    }
  });

  // Multi-pass deferred processing.
  //
  // Uses the same robust strategy as useVariableList:
  //   1. Before re-processing a node, DELETE any stale fallback entries it added
  //      during a previous pass so processNodeVariables can re-add them with the
  //      correct inferred type (bypasses the Set-based dedupe in addVariable).
  //   2. deferredNodeResolved reports TRUE only when the node's input reference
  //      actually matched a known variable in the list — NOT simply because we
  //      added 3 default-type fallback variables.
  //   3. If a full pass yields zero newly-resolved nodes, finalize the rest
  //      with fallbacks (the references are unresolvable / broken).
  const resolvedDeferred = new Set<string>();
  let remaining = deferredIds.filter(id => !resolvedDeferred.has(id));
  const maxPasses = Math.max(1, deferredIds.length);
  for (let pass = 0; pass < maxPasses && remaining.length > 0; pass++) {
    const passStartResolved = resolvedDeferred.size;
    const nowRemaining: string[] = [];
    for (const id of remaining) {
      const node = nodes.find(n => n.id === id);
      const nodeData = node?.getData();
      if (!nodeData) continue;

      removeVariablesForNode(list, keys, nodeData.id);
      processNodeVariables(nodeData, nodeData.id, list, keys);

      if (deferredNodeResolved(nodeData, list)) {
        resolvedDeferred.add(id);
      } else {
        nowRemaining.push(id);
      }
    }
    if (resolvedDeferred.size === passStartResolved) {
      nowRemaining.forEach(id => {
        const n = nodes.find(n => n.id === id);
        if (!n) return;
        removeVariablesForNode(list, keys, n.getData().id);
        processNodeVariables(n.getData(), n.getData().id, list, keys);
      });
      break;
    }
    remaining = nowRemaining;
  }

  return list;
};


// Recursively filter variables by types, with optional custom matcher
export const filterChildrenWithTypes = (
  variables: Suggestion[],
  types: string[],
  customMatcher?: (dataType: string) => boolean
): Suggestion[] => {
  return variables.flatMap((variable): Suggestion[] => {
    const matches = types.includes(variable.dataType) || (customMatcher?.(variable.dataType) ?? false)
    if (matches) {
      if (variable.children && variable.children?.length > 0) {
        const filteredChildren = filterChildrenWithTypes(variable.children, types, customMatcher)
        return [{ ...variable, children: filteredChildren, disabled: false }]
      }
      return [{ ...variable, disabled: false }]
    }
    if (variable.children && variable.children?.length > 0) {
      const filteredChildren = filterChildrenWithTypes(variable.children, types, customMatcher)
      if (filteredChildren.length > 0) {
        return [{ ...variable, disabled: true, children: filteredChildren }]
      }
    }
    return []
  })
}

/**
 * Hook for managing workflow variable list
 * 
 * @param {Node | null | undefined} selectedNode - Currently selected node
 * @param {React.MutableRefObject<Graph | undefined>} graphRef - Graph reference
 * @param {ChatVariable[]} chatVariables - List of chat variables
 * @param {EnvVariable[]} envVariables - List of environment variables
 * @returns {Suggestion[]} List of available variables
 */
export const useVariableList = (
  selectedNode: Node | null | undefined,
  graphRef: React.MutableRefObject<Graph | undefined>,
  chatVariables: ChatVariable[],
  envVariables: EnvVariable[],
  appType?: string
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

    // Collect relevant node IDs. Expand every relevant loop/iteration so an
    // iteration output can resolve variables produced by its child nodes even
    // when those children were added after the iteration was configured.
    const childIds = nodes.filter(n => n.getData()?.cycle === selectedNode.id).map(n => n.id);
    const parentLoop = getParentLoop(selectedNode.id);
    const relevantIds = new Set([
      ...getPreviousNodes(selectedNode.id),
      ...childIds,
      ...(parentLoop ? getPreviousNodes(parentLoop.id) : []),
    ]);

    let hasNewCycleChildren = true;
    while (hasNewCycleChildren) {
      hasNewCycleChildren = false;
      nodes.forEach(node => {
        const cycleId = node.getData()?.cycle;
        if (cycleId && relevantIds.has(cycleId) && !relevantIds.has(node.id)) {
          relevantIds.add(node.id);
          hasNewCycleChildren = true;
        }
      });
    }

    // Add system variables
    sysVariable.forEach((v: any) => {
        if (v?.name && !(appType === 'pure_workflow' && v.name === 'message')) {
          addVariable(list, keys, `sys_${v.name}`, `sys.${v.name}`, v.type, `sys.${v.name}`, { type: 'SYSTEM', name: 'SYSTEM', icon: '', id: 'SYSTEM' }, { group: 'SYSTEM' });
        }
      });
    // Add chat variables
    chatVariables?.forEach(v => addVariable(list, keys, `CONVERSATION_${v.name}`, v.name, v.type, `conv.${v.name}`, { type: 'CONVERSATION', name: 'CONVERSATION', icon: '', id: 'ENV' }, { group: 'CONVERSATION' }, v.defaultValue ?? v.default));
    envVariables?.forEach(v => addVariable(list, keys, `ENV_${v.name}`, v.name, v.value_type, `env.${v.name}`, { type: 'ENV', name: 'ENV', icon: '', id: 'ENV' }, { group: 'ENV' }));

    // Process each relevant node: deferred types last with multi-pass to resolve chains
    // list-operator A -> list-operator B requires A processed before B, but the DFS
    // order from output node may return [B, A, ...], so a single pass is insufficient.
    const deferredIds: string[] = [];
    relevantIds.forEach(id => {
      const node = nodes.find(n => n.id === id);
      if (!node) return;
      const t = node.getData()?.type;
      if (['var-aggregator', 'list-operator', 'iteration'].includes(t)) {
        deferredIds.push(id);
      } else {
        processNodeVariables(node.getData(), node.getData().id, list, keys);
      }
    });

    // Multi-pass deferred processing.
    //
    // Problem this two bugs that a simple "keys.size increased" as resolved check cannot detect:
    //   1. When the first pass adds a node with a missing upstream reference still
    //      adds the 3 default-type fallback variables (result/first_record/last_record).
    //      Its keys.size does increase, so the old algorithm marked it "resolved" even
    //      though it used a fallback type.
    //   2. addVariable() uses a Set to deduplicate by key, so a second call on a
    //      second pass with the correct type is a no-op (the key is already present).
    //
    // Fix strategy per pass:
    //   1. Before (re-)processing a still-unresolved node, DELETE any
    //      variables it added during a previous pass (removeVariablesForNode).
    //      This allows the subsequent processNodeVariables call to push variables with
    //      the CORRECT inferred type instead of being skipped by the key Set.
    //   2. After processing, check deferredNodeResolved - did its input
    //      actually reference a known variable? If yes, mark it truly resolved.
    //      If no, keep it for the next pass so its upstream deferred dependency
    //      has a chance to be computed first.
    //   3. If one full pass yields zero newly-resolved nodes, all remaining nodes
    //      have broken/missing references → let them finalize with fallbacks.
    const resolvedDeferred = new Set<string>();
    let remaining = deferredIds.filter(id => !resolvedDeferred.has(id));
    const maxPasses = Math.max(1, deferredIds.length);
    for (let pass = 0; pass < maxPasses && remaining.length > 0; pass++) {
      const passStartResolved = resolvedDeferred.size;
      const nowRemaining: string[] = [];
      for (const id of remaining) {
        const node = nodes.find(n => n.id === id);
        const nodeData = node?.getData();
        if (!nodeData) continue;

        // Wipe any stale fallback entries this node may have pushed on an
        // earlier pass so we can re-add them with the correct inferred type.
        removeVariablesForNode(list, keys, nodeData.id);
        processNodeVariables(nodeData, nodeData.id, list, keys);

        if (deferredNodeResolved(nodeData, list)) {
          resolvedDeferred.add(id);
        } else {
          nowRemaining.push(id);
        }
      }
      // No nodes made progress this pass → references are broken forever.
      // Finalize remaining nodes with their current fallback types.
      if (resolvedDeferred.size === passStartResolved) {
        nowRemaining.forEach(id => {
          const n = nodes.find(n => n.id === id);
          if (!n) return;
          removeVariablesForNode(list, keys, n.getData().id);
          processNodeVariables(n.getData(), n.getData().id, list, keys);
        });
        break;
      }
      remaining = nowRemaining;
    }

    // Add parent loop variables
    if (parentLoop) {
      const pd = parentLoop.getData();
      const pid = pd.id;
      if (pd.type === 'loop') {
        unwrapConfigArray<any>(pd.cycle_vars ?? pd.config?.cycle_vars).forEach((cv: any) => addVariable(list, keys, `${pid}_cycle_${cv.name}`, cv.name, cv.type || 'string', `${pid}.${cv.name}`, pd));
      } else if (pd.type === 'iteration') {
        const iterationInput = unwrapConfigPrimitive(pd.config?.input ?? pd.input);
        let itemType = 'string';
        if (iterationInput) {
          itemType = 'object';
          const iv = list.find(v => `{{${v.value}}}` === iterationInput);
          if (iv?.dataType.startsWith('array[')) {itemType = iv.dataType.replace(/^array\[(.+)\]$/, '$1');}
        }
        addVariable(list, keys, `${pid}_item`, 'item', itemType, `${pid}.item`, pd);
        addVariable(list, keys, `${pid}_index`, 'index', 'number', `${pid}.index`, pd);
      }
    }

    return list;
  }, [selectedNode, graphRef, trigger, chatVariables, envVariables, appType]);

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
