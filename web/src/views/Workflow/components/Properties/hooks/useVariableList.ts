import { useMemo, useEffect, useState } from 'react';
import { Graph, Node } from '@antv/x6';
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin';
import type { ChatVariable } from '../../../types';

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
    { label: 'status_code', dataType: 'number', field: 'status_code' }
  ],
  'question-classifier': [{ label: 'class_name', dataType: 'string', field: 'class_name' }],
  'memory-read': [
    { label: 'answer', dataType: 'string', field: 'answer' },
    { label: 'intermediate_outputs', dataType: 'array[object]', field: 'intermediate_outputs' }
  ]
} as const;

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
    list.push({ key, label, type: 'variable', dataType, value, nodeData, ...extra });
  }
};

const processNodeVariables = (
  nodeData: any,
  dataNodeId: string,
  variableList: Suggestion[],
  addedKeys: Set<string>
) => {
  const { type, config } = nodeData;

  if (type in NODE_VARIABLES) {
    NODE_VARIABLES[type as keyof typeof NODE_VARIABLES].forEach(({ label, dataType, field }) => {
      addVariable(variableList, addedKeys, `${dataNodeId}_${label}`, label, dataType, `${dataNodeId}.${field}`, nodeData);
    });
  }

  switch (type) {
    case 'start':
      [...(config?.variables?.defaultValue ?? []), ...(config?.variables?.value ?? [])].forEach((v: any) => {
        if (v?.name) addVariable(variableList, addedKeys, `${dataNodeId}_${v.name}`, v.name, v.type, `${dataNodeId}.${v.name}`, nodeData);
      });
      config?.variables?.sys?.forEach((v: any) => {
        if (v?.name) addVariable(variableList, addedKeys, `${dataNodeId}_sys_${v.name}`, `sys.${v.name}`, v.type, `sys.${v.name}`, nodeData);
      });
      break;

    case 'parameter-extractor':
      (config?.params?.defaultValue || []).forEach((p: any) => {
        if (p?.name) addVariable(variableList, addedKeys, `${dataNodeId}_${p.name}`, p.name, p.type || 'string', `${dataNodeId}.${p.name}`, nodeData);
      });
      break;

    case 'var-aggregator':
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
      let dt = 'string';
      if (nodeData.output) {
        const sv = variableList.find(v => v.value === nodeData.output);
        if (sv) dt = sv.dataType;
      }
      addVariable(variableList, addedKeys, `${dataNodeId}_output`, 'output', `array[${dt}]`, `${dataNodeId}.output`, nodeData);
      break;

    case 'loop':
      (config.cycle_vars.defaultValue || []).forEach((cv: any) => {
        if (cv.name?.trim()) addVariable(variableList, addedKeys, `${dataNodeId}_cycle_${cv.name}`, cv.name, cv.type || 'string', `${dataNodeId}.${cv.name}`, nodeData);
      });
      break;
  }
};

const hasOutputNodeTypes = [
  'llm',
  'knowledge-retrieval',
  'memory-read',
  'question-classifier',
  'var-aggregator',
  'http-request',
  'tool',
  'jinja-render'
]
export const getCurrentNodeVariables = (nodeData: any, values: any): Suggestion[] => {
  if (!nodeData || !hasOutputNodeTypes.includes(nodeData.type)) return [];
  const list: Suggestion[] = [];
  const keys = new Set<string>();
  const dataNodeId = nodeData.id;

  processNodeVariables({
    ...nodeData,
    config: {
      ...nodeData.config,
      ...values
    }
  }, dataNodeId, list, keys);
  return nodeData.type === 'var-aggregator' && !nodeData.config.group.defaultValue ? [] : list;
};

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

    const getPreviousNodes = (nodeId: string, visited = new Set<string>()): string[] => {
      if (visited.has(nodeId)) return [];
      visited.add(nodeId);
      const prev = edges.filter(e => e.getTargetCellId() === nodeId).map(e => e.getSourceCellId());
      return [...prev, ...prev.flatMap(id => getPreviousNodes(id, visited))];
    };

    const getParentLoop = (nodeId: string): Node | null => {
      const node = nodes.find(n => n.id === nodeId);
      const cycle = node?.getData()?.cycle;
      if (cycle) {
        const parent = nodes.find(n => n.getData().id === cycle);
        if (parent?.getData()?.type === 'loop' || parent?.getData()?.type === 'iteration') return parent;
      }
      return null;
    };

    const childIds = nodes.filter(n => n.getData()?.cycle === selectedNode.id).map(n => n.id);
    const parentLoop = getParentLoop(selectedNode.id);
    const relevantIds = [...getPreviousNodes(selectedNode.id), ...childIds, ...(parentLoop ? getPreviousNodes(parentLoop.id) : [])];

    chatVariables?.forEach(v => addVariable(list, keys, `CONVERSATION_${v.name}`, v.name, v.type, `conv.${v.name}`, { type: 'CONVERSATION', name: 'CONVERSATION', icon: '' }, { group: 'CONVERSATION' }));

    relevantIds.forEach(id => {
      const node = nodes.find(n => n.id === id);
      if (node) processNodeVariables(node.getData(), node.getData().id, list, keys);
    });

    if (parentLoop) {
      const pd = parentLoop.getData();
      const pid = pd.id;
      if (pd.type === 'loop') {
        (pd.cycle_vars || []).forEach((cv: any) => addVariable(list, keys, `${pid}_cycle_${cv.name}`, cv.name, cv.type || 'String', `${pid}.${cv.name}`, pd));
      } else if (pd.type === 'iteration' && pd.config.input.defaultValue) {
        let itemType = 'object';
        const iv = list.find(v => `{{${v.value}}}` === pd.config.input.defaultValue);
        if (iv?.dataType.startsWith('array[')) itemType = iv.dataType.replace(/^array\[(.+)\]$/, '$1');
        addVariable(list, keys, `${pid}_item`, 'item', itemType, `${pid}.item`, pd);
        addVariable(list, keys, `${pid}_index`, 'index', 'number', `${pid}.index`, pd);
      }
    }

    return list;
  }, [selectedNode, graphRef, trigger, chatVariables]);

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
