import type { Graph, Node, Edge } from '@antv/x6';
import type { RefObject, Dispatch, SetStateAction, MutableRefObject } from 'react';
import type { TFunction } from 'i18next';

import { edgeAttrs, edge_color, edge_selected_color, edge_width, graphNodeLibrary } from '../../constant';

/**
 * Context required to create the interactive graph event handlers.
 */
export interface GraphHandlersCtx {
  graphRef: MutableRefObject<Graph | undefined>;
  containerRef: RefObject<HTMLDivElement>;
  setSelectedNode: Dispatch<SetStateAction<Node | null>>;
  setZoomLevel: Dispatch<SetStateAction<number>>;
  setRunOpen: Dispatch<SetStateAction<boolean>>;
  isHandModeRef: MutableRefObject<boolean>;
  t: TFunction;
}

/**
 * Build all interactive event handlers for the workflow graph. Keeping them in
 * one factory preserves their mutual references (blankClick / clearNodeSelect...).
 */
export const createGraphHandlers = ({
  graphRef,
  containerRef,
  setSelectedNode,
  setZoomLevel,
  setRunOpen,
  isHandModeRef,
  t,
}: GraphHandlersCtx) => {
  /**
   * Handle node click event
   * @param node - Clicked node
   */
  const nodeClick = ({ node }: { node: Node }) => {
    setRunOpen(false)
    // add-node type: dispatch port:click to open node selection popover
    // Must handle before blankClick() to avoid blank:click closing the popover immediately
    const nodeData = node.getData()
    if (nodeData?.type === 'add-node') {
      const b = node.getBBox();
      const screenPos = graphRef.current!.localToClient(b.x + b.width, b.y + b.height / 2);
      const tempDiv = document.createElement('div');
      tempDiv.style.cssText = `position:fixed;left:${screenPos.x}px;top:${screenPos.y}px;width:1px;height:1px;z-index:9999;`;
      document.body.appendChild(tempDiv);
      window.dispatchEvent(new CustomEvent('port:click', {
        detail: {
          node,
          port: 'right',
          element: tempDiv,
          rect: { left: screenPos.x, top: screenPos.y },
          edgeInsertion: null,
        },
      }));
      return;
    }

    blankClick()

    setTimeout(() => {
    // Ignore add-node type node clicks
      const nodeData = node.getData()
      if (nodeData.type === 'break' || nodeData.type === 'cycle-start') {
        setSelectedNode(null)
        return;
      }
      clearNodeSelect()
      node.setData({
        ...nodeData,
        isSelected: true,
      });
      clearEdgeSelect()
      if (nodeData.type !== 'notes') {
        setSelectedNode(node);
      }
    }, 0)
  };
  /**
   * Handle edge click event
   * @param edge - Clicked edge
   */
  const edgeClick = ({ edge }: { edge: Edge }) => {
    clearEdgeSelect();
    edge.setAttrByPath('line/stroke', edge_selected_color);
    edge.setData({ ...edge.getData(), isSelected: true }, { silent: true });
    clearNodeSelect();
  };
  /**
   * Clear all selected nodes
   */
  const clearNodeSelect = () => {
    const nodes = graphRef.current?.getNodes();

    nodes?.forEach(node => {
      const data = node.getData();
      if (data.isSelected) {
        node.setData({
          ...data,
          isSelected: false,
        });
      }
    });
    setSelectedNode(null);
  };
  /**
   * Clear all selected edges
   */
  const clearEdgeSelect = () => {
    graphRef.current?.getEdges().forEach(e => {
      e.setData({ ...e.getData(), isSelected: false, isNodeHover: false }, { silent: true });
      e.setAttrByPath('line/stroke', edge_color);
      e.setAttrByPath('line/strokeWidth', edge_width);
    });
  };
  /**
   * Handle blank canvas click - deselect all
   */
  const blankClick = () => {
    clearNodeSelect();
    clearEdgeSelect();
    graphRef.current?.cleanSelection();
    setSelectedNode(null);
    window.dispatchEvent(new CustomEvent('blank:click'));
  };
  /**
   * Handle canvas scale/zoom event
   * @param sx - Scale factor on x-axis
   */
  const scaleEvent = ({ sx }: { sx: number }) => {
    setZoomLevel(sx);
  };
  /**
   * Handle node moved event - restrict child nodes within parent bounds
   * @param node - Moved node
   */
  const nodeMoved = ({ node }: { node: Node }) => {
    const cycle = node.getData()?.cycle;
    if (cycle) {
      const parentNode = graphRef.current!.getNodes().find(n => n.id === cycle);
      const parentType = parentNode?.getData()?.type;
      if (parentNode?.getData()?.isGroup || (parentNode && (parentType === 'loop' || parentType === 'iteration'))) {
        // Get parent node and child node bounding boxes
        const parentBBox = parentNode.getBBox();
        const childBBox = node.getBBox();

        // Calculate parent node padding
        const padding = 24;
        const headerHeight = 50;

        // Calculate minimum and maximum positions allowed for child node
        const minX = parentBBox.x + padding;
        const minY = parentBBox.y + padding + headerHeight;
        const maxX = parentBBox.x + parentBBox.width - padding - childBBox.width;
        const maxY = parentBBox.y + parentBBox.height - padding - childBBox.height;

        // Restrict child node movement within parent node
        let newX = childBBox.x;
        let newY = childBBox.y;

        if (newX < minX) newX = minX;
        if (newY < minY) newY = minY;
        if (newX > maxX) newX = maxX;
        if (newY > maxY) newY = maxY;

        // If child node position is restricted, update its position
        if (newX !== childBBox.x || newY !== childBBox.y) {
          node.setPosition(newX, newY);
        }
      }
    }
  };
  /**
   * Handle copy keyboard shortcut (Ctrl+C / Cmd+C)
   * @returns false to prevent default behavior
   */
  const copyEvent = () => {
    if (!graphRef.current) return false;
    let selectedNodes = []
    if (isHandModeRef.current) {
      selectedNodes = graphRef.current.getNodes().filter(node => node.getData()?.isSelected);
    } else {
     selectedNodes = graphRef.current.getSelectedCells();
    }
    if (selectedNodes.length) {
      graphRef.current.copy(selectedNodes);
    }
    return false;
  };
  /**
   * Handle paste keyboard shortcut (Ctrl+V / Cmd+V)
   * @returns false to prevent default behavior
   */
  const parseEvent = () => {
    if (!graphRef.current?.isClipboardEmpty()) {
      graphRef.current?.startBatch('copy');
      const pastedNodes = graphRef.current?.paste({ offset: 32 }) ?? [];
      pastedNodes.forEach(cell => {
        if (cell.isNode()) {
          const data = cell.getData();
          const newId = `${(data.type as string).replace(/-/g, '_')}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
          cell.setData({ ...data, id: newId });
        }
      });
      blankClick();
      graphRef.current?.stopBatch('copy');
    }
    return false;
  };
  /**
   * Handle delete keyboard shortcut
   * Removes selected nodes, edges, and handles parent-child relationships
   * @returns false to prevent default behavior
   */
  const deleteEvent = () => {
    if (!graphRef.current) return;
    const nodes = graphRef.current?.getNodes();
    const edges = graphRef.current?.getEdges();
    const cells: (Node | Edge)[] = [];
    const nodesToDelete: Node[] = [];
    const parentNodesToUpdate: Node[] = [];

    // First collect all selected nodes, but exclude default child nodes
    nodes?.forEach(node => {
      const data = node.getData();
      // If node is default child node, do not allow individual deletion
      if (data.isSelected && !data.isDefault) {
        nodesToDelete.push(node);
      }
    });

    // Collect edges related to selected nodes
    edges?.forEach(edge => {
      const attrs = edge.getAttrs()
      if (attrs.line.stroke === edge_selected_color) {
        cells.push(edge)
      }
      const sourceId = edge.getSourceCellId();
      const targetId = edge.getTargetCellId();
      if (sourceId && targetId) {
        const sourceNode = nodes?.find(n => n.id === sourceId);
        const targetNode = nodes?.find(n => n.id === targetId);
        if (sourceNode?.getData()?.isSelected || targetNode?.getData()?.isSelected) {
          cells.push(edge);
        }
      }
    })

    // For each selected node
    if (nodesToDelete.length > 0) {
      nodesToDelete.forEach(nodeToDelete => {
        // Check if it's a child node
        const nodeData = nodeToDelete.getData();
        if (nodeData.cycle) {
          // Find corresponding parent node
          const parentNode = nodes?.find(n => n.id === nodeData.cycle);
          if (parentNode) {
            parentNodesToUpdate.push(parentNode);
          }
          // Add child node to deletion list
          cells.push(nodeToDelete);
        }
        // Check if it's LoopNode, IterationNode or SubGraphNode
        else if (nodeToDelete.shape === 'loop-node' || nodeToDelete.shape === 'iteration-node' || nodeToDelete.shape === 'subgraph-node') {
          // Find all child nodes with cycle equal to current node id
          nodes?.forEach(node => {
            const data = node.getData();
            if (data.cycle === nodeToDelete.id || data.cycle === nodeToDelete.getData()?.id) {
              cells.push(node);
            }
          });
          // Add parent node to deletion list
          cells.push(nodeToDelete);
        }
        // Normal node
        else {
          cells.push(nodeToDelete);
        }
      });
      blankClick();
    }

    // Delete all collected nodes and edges
    if (cells.length > 0) {
      // Pre-calculate which parents need an add-node restored (before removal changes the graph)
      const parentsNeedingAddNode = parentNodesToUpdate
        .filter(parentNode => {
          const parentShape = parentNode.shape;
          if (parentShape !== 'loop-node' && parentShape !== 'iteration-node') return false;
          const parentData = parentNode.getData();
          const allChildren = graphRef.current!.getNodes().filter(n => n.getData()?.cycle === parentData.id);
          const cycleStartNodes = allChildren.filter(n => n.getData()?.type === 'cycle-start');
          // After deletion, only cycle-start will remain
          const nonCycleStartToDelete = cells.filter(c =>
            c.isNode() &&
            (c as Node).getData()?.cycle === parentData.id &&
            (c as Node).getData()?.type !== 'cycle-start'
          );
          return cycleStartNodes.length === 1 && (allChildren.length - nonCycleStartToDelete.length) === 1;
        })
        .map(parentNode => ({
          parentNode,
          cycleStartNode: graphRef.current!.getNodes().find(
            n => n.getData()?.cycle === parentNode.getData().id && n.getData()?.type === 'cycle-start'
          )!
        }))
        .filter(({ cycleStartNode }) => !!cycleStartNode);

      graphRef.current?.startBatch('delete');
      graphRef.current?.removeCells(cells);

      parentsNeedingAddNode.forEach(({ parentNode, cycleStartNode }) => {
        const parentData = parentNode.getData();
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
      });

      graphRef.current?.stopBatch('delete');
    }
    return false;
  };
  const nodePortClickEvent = ({ e, node, port }: { e: MouseEvent, node: Node, port: string }) => {
    e.stopPropagation();
    e.preventDefault();
    const portElement = e.target as HTMLElement;
    const rect = portElement.getBoundingClientRect();
    const clickPort = node.getPorts().find(p => p.id === port)
    const portGroup = clickPort?.group

    if (portGroup === 'left') {
      return
    }

    // Create temporary popover trigger element
    const tempDiv = document.createElement('div');
    tempDiv.style.position = 'fixed';
    tempDiv.style.left = rect.left + 'px';
    tempDiv.style.top = rect.top + 'px';
    tempDiv.style.width = '1px';
    tempDiv.style.height = '1px';
    tempDiv.style.zIndex = '9999';
    document.body.appendChild(tempDiv);

    // Trigger custom event to show node selection popover
    const customEvent = new CustomEvent('port:click', {
      detail: { node, port, element: tempDiv, rect }
    });
    window.dispatchEvent(customEvent);
    clearNodeSelect();
  }

  /**
   * Handle window resize event
   */
  const handleResize = () => {
    if (containerRef.current && graphRef.current) {
      graphRef.current.resize(containerRef.current.offsetWidth, containerRef.current.offsetHeight);
    }
  };

  return {
    nodeClick,
    edgeClick,
    clearNodeSelect,
    clearEdgeSelect,
    blankClick,
    scaleEvent,
    nodeMoved,
    copyEvent,
    parseEvent,
    deleteEvent,
    nodePortClickEvent,
    handleResize,
  }
}
