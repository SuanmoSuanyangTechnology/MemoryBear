import type { Graph, Node } from '@antv/x6'

import type { Suggestion } from '../Editor/plugin/AutocompletePlugin'
import { getChildNodeVariables, filterChildrenWithTypes } from './hooks/useVariableList'

/**
 * Get filtered variable list based on node type and config key.
 * Pure helper extracted from the Properties component; it closes over the
 * selected node, the graph reference and the full variable list which are
 * passed in explicitly instead of relying on component scope.
 * @param selectedNode - Currently selected node
 * @param graphRef - Reference to graph instance
 * @param variableList - Full list of available variables
 * @param nodeType - Type of the node
 * @param key - Configuration key
 * @returns Filtered variable list
 */
export const getFilteredVariableList = (
  selectedNode: Node | undefined,
  graphRef: React.MutableRefObject<Graph | undefined>,
  variableList: Suggestion[],
  nodeType?: string,
  key?: string,
): Suggestion[] => {
  // Check if current node is a child of iteration node
  const parentIterationNode = selectedNode ? (() => {
    const nodes = graphRef.current?.getNodes() || [];
    const nodeData = selectedNode.getData();
    const cycle = nodeData?.cycle;

    if (cycle) {
      const parentNode = nodes.find(n => n.getData().id === cycle);
      if (parentNode) {
        const parentData = parentNode.getData();
        if (parentData?.type === 'iteration') {
          return parentNode;
        }
      }
    }
    return null;
  })() : null;

  // Helper function to add parent iteration variables
  const addParentIterationVars = (filteredList: any[]) => {
    if (parentIterationNode) {
      const parentData = parentIterationNode.getData();
      const parentNodeId = parentData.id;

      if (parentData.config?.input?.defaultValue) {
        const itemKey = `${parentNodeId}_item`;
        const indexKey = `${parentNodeId}_index`;

        const existingItemVar = filteredList.find(v => v.key === itemKey);
        const existingIndexVar = filteredList.find(v => v.key === indexKey);

        if (!existingItemVar) {
          // Determine item dataType from input variable
          let itemDataType = 'object';
          const inputVariable = variableList.find(v => `{{${v.value}}}` === parentData.config.input.defaultValue);
          if (inputVariable && inputVariable.dataType.startsWith('array[')) {
            itemDataType = inputVariable.dataType.replace(/^array\[(.+)\]$/, '$1');
          }

          filteredList.push({
            key: itemKey,
            label: 'item',
            type: 'variable',
            dataType: itemDataType,
            value: `${parentNodeId}.item`,
            nodeData: parentData,
          });
        }

        if (!existingIndexVar) {
          filteredList.push({
            key: indexKey,
            label: 'index',
            type: 'variable',
            dataType: 'number',
            value: `${parentNodeId}.index`,
            nodeData: parentData,
          });
        }
      }
    }
    return filteredList;
  };

  if (nodeType === 'llm') {
    // For LLM nodes that are children of iteration or loop nodes, include parent variables
    const parentLoopNode = selectedNode ? (() => {
      const nodes = graphRef.current?.getNodes() || [];
      const nodeData = selectedNode.getData();
      const cycle = nodeData?.cycle;

      if (cycle) {
        const parentNode = nodes.find(n => n.getData().id === cycle);
        if (parentNode) {
          const parentData = parentNode.getData();
          if (parentData?.type === 'loop' || parentData?.type === 'iteration') {
            return parentNode;
          }
        }
      }
      return null;
    })() : null;

    let filteredList = variableList.filter(variable => !['boolean', 'object', 'array[boolean]'].includes(variable.dataType));

    // If this LLM node is a child of iteration/loop, ensure parent variables are included
    if (parentLoopNode) {
      const parentData = parentLoopNode.getData();
      const parentNodeId = parentData.id;

      // Ensure parent loop/iteration variables are included
      if (parentData.type === 'loop') {
        const cycleVars = parentData.cycle_vars || [];
        cycleVars.forEach((cycleVar: any) => {
          const key = `${parentNodeId}_cycle_${cycleVar.name}`;
          const existingVar = filteredList.find(v => v.key === key);
          if (!existingVar && cycleVar.name && cycleVar.type !== 'boolean') {
            filteredList.push({
              key,
              label: cycleVar.name,
              type: 'variable',
              dataType: cycleVar.type || 'string',
              value: `${parentNodeId}.${cycleVar.name}`,
              nodeData: parentData,
            });
          }
        });
      } else if (parentData.type === 'iteration') {
        // Add item and index variables for iteration parent
        if (parentData.config?.input?.defaultValue) {
          const itemKey = `${parentNodeId}_item`;
          const indexKey = `${parentNodeId}_index`;

          const existingItemVar = filteredList.find(v => v.key === itemKey);
          const existingIndexVar = filteredList.find(v => v.key === indexKey);

          if (!existingItemVar) {
            // Determine item dataType from input variable
            let itemDataType = 'object';
            const inputVariable = variableList.find(v => `{{${v.value}}}` === parentData.config.input.defaultValue);
            if (inputVariable && inputVariable.dataType.startsWith('array[')) {
              itemDataType = inputVariable.dataType.replace(/^array\[(.+)\]$/, '$1');
            }

            filteredList.push({
              key: itemKey,
              label: 'item',
              type: 'variable',
              dataType: itemDataType,
              value: `${parentNodeId}.item`,
              nodeData: parentData,
            });
          }

          if (!existingIndexVar) {
            filteredList.push({
              key: indexKey,
              label: 'index',
              type: 'variable',
              dataType: 'Number',
              value: `${parentNodeId}.index`,
              nodeData: parentData,
            });
          }
        }
      }
    }

    return filteredList;
  }
  if (nodeType === 'knowledge-retrieval') {
    const allList = addParentIterationVars(variableList);
    let filteredList: Suggestion[] = []

    allList.forEach(variable => {
      if (variable.dataType === 'string') {
        filteredList.push(variable)
      } else if (variable.dataType === 'file') {
        // Recursively filter string children from file type
        const filteredFile = filterChildrenWithTypes([variable], ['string'])[0];
        if (filteredFile) {
          filteredList.push(filteredFile);
        }
      } else if (variable.children && variable.children?.length > 0) {
        // Recursively handle other types with children
        const filteredVar = filterChildrenWithTypes([variable], ['string'])[0];
        if (filteredVar) {
          filteredList.push(filteredVar);
        }
      }
    })

    return filteredList
  }
  if ((nodeType === 'parameter-extractor' && key === 'text')
    || (nodeType === 'question-classifier' && ['input_variable', 'categories'].includes(key as string))
  ) {
    const allList = addParentIterationVars(variableList);
    let filteredList: Suggestion[] = []
    allList.forEach(variable => {
      if (variable.dataType === 'string') {
        filteredList.push(variable)
      } else if (variable.dataType === 'file') {
        filteredList.push({
          ...variable,
          children: variable.children.filter((child: Suggestion) => child.dataType === 'string')
        })
      } else if (variable.children && variable.children?.length > 0) {
        // Recursively handle other types with children
        const filteredVar = filterChildrenWithTypes([variable], ['string'])[0];
        if (filteredVar) {
          filteredList.push(filteredVar);
        }
      }
    })

    return filteredList
  }

  if ((nodeType === 'parameter-extractor' && key === 'prompt')
    || (nodeType === 'question-classifier' && key === 'user_supplement_prompt')
    || nodeType === 'human-intervention'
  ) {
    const allList = addParentIterationVars(variableList);
    let filteredList: Suggestion[] = []
    allList.forEach(variable => {
      if (['string', 'number'].includes(variable.dataType)) {
        filteredList.push(variable)
      } else if (variable.dataType === 'file') {
        // Recursively filter string/number children from file type
        const filteredFile = filterChildrenWithTypes([variable], ['string', 'number'])[0];
        if (filteredFile) {
          filteredList.push(filteredFile);
        }
      } else if (variable.children && variable.children?.length > 0) {
        // Recursively handle other types with children
        const filteredVar = filterChildrenWithTypes([variable], ['string', 'number'])[0];
        if (filteredVar) {
          filteredList.push(filteredVar);
        }
      }
    })

    return filteredList
  }
  if (nodeType === 'memory-read') {
    const allList = addParentIterationVars(variableList);
    let filteredList: Suggestion[] = []
    allList.forEach(variable => {
      if (variable.dataType === 'string') {
        filteredList.push(variable)
      } else if (variable.dataType === 'file') {
      } else if (variable.children && variable.children?.length > 0) {
        // Recursively handle other types with children
        const filteredVar = filterChildrenWithTypes([variable], ['string'])[0];
        if (filteredVar) {
          filteredList.push(filteredVar);
        }
      }
    })
    return filteredList;
  }
  if (nodeType === 'memory-write') {
    const allList = addParentIterationVars(variableList);
    let filteredList: Suggestion[] = []
    allList.forEach(variable => {
      if (['string', 'array[file]'].includes(variable.dataType)) {
        filteredList.push(variable)
      } else if (variable.dataType === 'file') {
        filteredList.push({
          ...variable,
          children: variable.children.filter((child: Suggestion) => child.dataType === 'string')
        })
      } else if (variable.children && variable.children?.length > 0) {
        // Recursively handle other types with children
        const filteredVar = filterChildrenWithTypes([variable], ['string'])[0];
        if (filteredVar) {
          filteredList.push(filteredVar);
        }
      }
    })

    return filteredList
  }

  if ((nodeType === 'iteration' && key === 'output')) {
    if (!selectedNode) return [];
    let filteredList = variableList.filter(variable => variable.value.includes('sys.') || variable.nodeData?.type === 'var-aggregator')
    const childVariables = getChildNodeVariables(selectedNode, graphRef);
    const existingKeys = new Set(filteredList.map(v => v.key));
    childVariables.forEach(v => {
      if (!existingKeys.has(v.key)) {
        filteredList.push(v);
        existingKeys.add(v.key);
      }
    });

    return filteredList.filter(variable => variable.dataType !== 'array[file]');
  }
  if (nodeType === 'loop' && key === 'condition') {
    if (!selectedNode) return [];
    let filteredList = addParentIterationVars(variableList).filter(variable => variable.nodeData.type !== 'loop');

    const childVariables = getChildNodeVariables(selectedNode, graphRef);
    const existingKeys = new Set(filteredList.map(v => v.key));
    childVariables.forEach(v => {
      if (!existingKeys.has(v.key)) {
        filteredList.push(v);
        existingKeys.add(v.key);
      }
    });

    return filteredList;
  }
  if (nodeType === 'iteration') {
    return variableList.filter(variable => variable.dataType.includes('array'));
  }

  if ((nodeType === 'if-else' && key === 'cases')) {
    const allList = addParentIterationVars(variableList);
    let filteredList: Suggestion[] = []
    allList.forEach(variable => {
      if (variable.dataType === 'file') {
        filteredList.push({
          ...variable,
          disabled: true,
        })
      } else {
        filteredList.push(variable)
      }
    })

    return filteredList
  }

  if (nodeType === 'var-aggregator' || nodeType === 'assigner' || nodeType === 'jinja-render') {
    return variableList.filter(variable => variable.dataType !== 'secret');
  }
  if (nodeType === 'agent' && key === 'context') {
    return variableList.filter(variable => variable.dataType === 'array[object]');
  }

  // For all other node types, add parent iteration variables if applicable
  let baseList = variableList;
  return addParentIterationVars(baseList);
};
