import { Graph } from '@antv/x6';
import type { Node, Edge } from '@antv/x6';
import { register as registerReactShape } from '@antv/x6-react-shape';
import type { RefObject, MutableRefObject } from 'react';
import { createElement } from 'react';
import { createRoot } from 'react-dom/client';

import { edgeAttrs, edgeHoverTool, edge_color, edge_selected_color, edge_width, nodeRegisterLibrary } from '../../constant';
import { reorderCells } from './reorderCells';
import { isSafari } from './env';

/**
 * Context required to build the graph `init` function.
 */
export interface GraphInitCtx {
  containerRef: RefObject<HTMLDivElement>;
  miniMapRef: RefObject<HTMLDivElement>;
  graphRef: MutableRefObject<Graph | undefined>;
  isHandMode: boolean;
  setupPlugins: () => void;
  nodeClick: (args: { node: Node }) => void;
  edgeClick: (args: { edge: Edge }) => void;
  nodePortClickEvent: (args: { e: MouseEvent; node: Node; port: string }) => void;
  blankClick: () => void;
  scaleEvent: (args: { sx: number }) => void;
  nodeMoved: (args: { node: Node }) => void;
  copyEvent: () => boolean | void;
  parseEvent: () => boolean | void;
  deleteEvent: () => boolean | void;
  undo: () => void;
  redo: () => void;
}

/**
 * Create the async `init` function that instantiates the X6 graph and wires up
 * all of its event listeners. Kept as a factory so the returned closure can
 * access the handlers passed in via ctx.
 */
export const createGraphInit = ({
  containerRef,
  miniMapRef,
  graphRef,
  isHandMode,
  setupPlugins,
  nodeClick,
  edgeClick,
  nodePortClickEvent,
  blankClick,
  scaleEvent,
  nodeMoved,
  copyEvent,
  parseEvent,
  deleteEvent,
  undo,
  redo,
}: GraphInitCtx) => {
  /**
   * Initialize X6 graph with configuration and event listeners
   */
  const init = async () => {
    if (!containerRef.current || !miniMapRef.current) return;

    // Register React shapes
    // Safari: use x6-html-shape to avoid foreignObject rendering issues
    if (isSafari) {
      const { register: registerHtmlShape } = await import('x6-html-shape');
      nodeRegisterLibrary.forEach(({ shape, width, height, component }) => {
        registerHtmlShape({
          shape,
          width,
          height,
          render(node: Node, _graph: unknown, container: HTMLElement) {
            const root = createRoot(container);
            const doRender = () => {
              root.render(createElement(component as any, { node, graph: node.model?.graph, data: node.getData() }));
            };
            doRender();
            node.on('change:data', doRender);
            return () => {
              node.off('change:data', doRender);
              root.unmount();
            };
          },
        });
      });
    } else {
      nodeRegisterLibrary.forEach((item) => {
        registerReactShape(item);
      });
    }

    const container = containerRef.current;
    graphRef.current = new Graph({
      container,
      background: {
        color: '#F0F3F8',
      },
      autoResize: true,
      grid: {
        visible: true,
        type: 'dot',
        size: 10,
        args: {
          color: '#939AB1', // Grid dot color
          thickness: 1, // Grid dot size
        }
      },
      panning: isHandMode,
      mousewheel: {
        enabled: true,
        factor: 0.1,
        modifiers: null,
      },
      connecting: {
        connector: {
          name: 'smooth',
          args: {
            radius: 8,
          },
        },
        anchor: 'midSide',
        connectionPoint: 'anchor',
        allowBlank: false,
        allowLoop: false,
        allowNode: false,
        allowEdge: false,
        allowPort: true,
        allowMulti: true,
        highlight: true,
        snap: {
          radius: 20,
        },
        createEdge() {
          return graphRef.current?.createEdge(edgeAttrs);
        },
        validateConnection({ sourceCell, targetCell, sourceMagnet, targetMagnet }) {
          if (!targetMagnet) return false;

          // Only allow right port → left port connections
          const getPortGroup = (magnet: Element) => {
            let el: Element | null = magnet;
            while (el) {
              const group = el.getAttribute('port-group');
              if (group) return group;
              el = el.parentElement;
            }
            return null;
          };
          const sourceGroup = sourceMagnet ? getPortGroup(sourceMagnet) : null;
          const targetGroup = targetMagnet ? getPortGroup(targetMagnet) : null;

          if (sourceGroup === 'left' || targetGroup === 'right') return false;

          // Node cannot connect to itself
          if (sourceCell?.id === targetCell?.id) return false;

          const targetType = targetCell?.getData()?.type;

          // Start node cannot be connection target
          if (targetType === 'start') return false;

          // Get source node and target node parent IDs
          const sourceParentId = sourceCell?.getData()?.cycle;
          const targetParentId = targetCell?.getData()?.cycle;

          // Validate parent-child relationship:
          // 1. If both nodes have parent IDs, they must be same to connect
          // 2. If both have no parent ID, can connect normally
          // 3. If one has parent, one doesn't, cannot connect
          if (sourceParentId && targetParentId) {
            // Child nodes under same parent can connect to each other
            if (sourceParentId !== targetParentId) return false;
          } else if (sourceParentId || targetParentId) {
            // One has parent, one doesn't, cannot connect
            return false;
          }

          // Prevent duplicate connections between same ports
          const sourcePortId = sourceMagnet?.getAttribute('port') ?? sourceMagnet?.closest('[port]')?.getAttribute('port');
          const targetPortId = targetMagnet?.getAttribute('port') ?? targetMagnet?.closest('[port]')?.getAttribute('port');
          const duplicate = graphRef.current?.getEdges().some(e =>
            e.getSourceCellId() === sourceCell?.id &&
            e.getTargetCellId() === targetCell?.id &&
            e.getSourcePortId() === sourcePortId &&
            e.getTargetPortId() === targetPortId
          );
          if (duplicate) return false;

          return true;
        },
      },
      embedding: {
        enabled: false,
      },
      translating: {
        restrict(view) {
          if (!view) return null
          const cell = view.cell
          if (cell.isNode()) {
            // Parent (iteration/loop) nodes are not restricted
            if (cell.getData()?.type === 'iteration' || cell.getData()?.type === 'loop') return null
            const parent = cell.getParent()
            if (parent) {
              return parent.getBBox()
            }
          }
          return null
        },
      },
      highlighting: {
        embedding: {
          name: 'stroke',
          args: {
            padding: -1,
            attrs: {
              stroke: '#73d13d',
            },
          },
        },
      },
    });
    // Use plugins
    setupPlugins();
    // Listen to edge mouseenter event: show hover style and add button
    graphRef.current.on('edge:mouseenter', ({ edge }: { edge: Edge }) => {
      setTimeout(() => {
        edge.addTools([edgeHoverTool]);
      }, 0)
    });
    // Listen to edge mouseleave event: revert style and remove add button
    graphRef.current.on('edge:mouseleave', ({ edge }: { edge: Edge }) => {
      const data = edge.getData();
      if (!data?.isSelected) {
        if (data?.isNodeHover) {
          edge.setAttrByPath('line/stroke', edge_selected_color);
        } else {
          edge.setAttrByPath('line/stroke', edge_color);
          edge.setAttrByPath('line/strokeWidth', edge_width);
        }
      }
      edge.removeTools();
    });
    // Listen to node selection event
    graphRef.current.on('node:click', nodeClick);
    // Listen to edge selection event
    graphRef.current.on('edge:click', edgeClick);
    // Listen to port click event
    graphRef.current.on('node:port:click', nodePortClickEvent);
    // Listen to canvas click event, cancel selection
    graphRef.current.on('blank:click', blankClick);
    // Node hover: highlight connected edges
    graphRef.current.on('node:mouseenter', ({ node }) => {
      graphRef.current?.getEdges().forEach(edge => {
        const view = graphRef.current?.findViewByCell(edge);
        view?.removeTools();
        if (!edge.getData()?.isSelected && edge.getAttrByPath('line/stroke') === edge_selected_color) {
          edge.setAttrByPath('line/stroke', edge_color);
        }
      });
      graphRef.current?.getConnectedEdges(node).forEach(edge => {
        if (!edge.getData()?.isSelected) {
          edge.setAttrByPath('line/stroke', edge_selected_color);
          edge.setData({ ...edge.getData(), isNodeHover: true }, { silent: true });
        }
      });
    });
    graphRef.current.on('node:mouseleave', ({ node }) => {
      graphRef.current?.getConnectedEdges(node).forEach(edge => {
        if (!edge.getData()?.isSelected) {
          edge.setAttrByPath('line/stroke', edge_color);
          edge.setData({ ...edge.getData(), isNodeHover: false }, { silent: true });
        }
      });
    });
    // Listen to zoom event
    graphRef.current.on('scale', scaleEvent);
    // Listen to node move event
    graphRef.current.on('node:moved', nodeMoved);

    if (isSafari) {
      // When a parent (loop/iteration) node moves, keep child nodes in sync.
      // Store each child's offset relative to the parent at drag start, then
      // reapply it every frame to avoid cumulative delta errors.
      const dragOffsets = new Map<string, { dx: number; dy: number }>();

      graphRef.current.on('node:moving', ({ node }: { node: Node }) => {
        const data = node.getData();
        if (data?.type !== 'loop' && data?.type !== 'iteration') return;
        const pos = node.getPosition();
        const PORT_RADIUS = 6;

        // Update parent componentContainer directly
        const parentView = graphRef.current?.findViewByCell(node) as any;
        if (parentView?.componentContainer) {
          parentView.componentContainer.style.transform =
            `translate(${pos.x + PORT_RADIUS}px, ${pos.y}px)`;
        }

        const children = graphRef.current?.getNodes().filter(child => {
          const cycle = child.getData()?.cycle;
          return cycle === data.id || cycle === node.id;
        }) ?? [];

        // First event for this drag: record offsets
        if (!dragOffsets.has(node.id)) {
          children.forEach(child => {
            const cp = child.getPosition();
            dragOffsets.set(child.id, { dx: cp.x - pos.x, dy: cp.y - pos.y });
          });
        }

        // Apply stored offsets to keep children in place relative to parent
        children.forEach(child => {
          const off = dragOffsets.get(child.id);
          if (!off) return;
          const nx = pos.x + off.dx;
          const ny = pos.y + off.dy;
          child.setPosition(nx, ny);
          const childView = graphRef.current?.findViewByCell(child) as any;
          if (childView?.componentContainer) {
            childView.componentContainer.style.transform =
              `translate(${nx + PORT_RADIUS}px, ${ny}px)`;
          }
        });
      });

      graphRef.current.on('node:moved', ({ node }: { node: Node }) => {
        // Clear offsets for this parent and all its children
        const data = node.getData();
        graphRef.current?.getNodes().forEach(child => {
          const cycle = child.getData()?.cycle;
          if (cycle === data?.id || cycle === node.id) dragOffsets.delete(child.id);
        });
        dragOffsets.delete(node.id);
        nodeMoved({ node });
      });
    }

    graphRef.current.on('node:removed', blankClick)
    // When edge connected, reorder all cells to maintain correct layer order
    graphRef.current.on('edge:connected', ({ isNew, edge }) => {
      if (isSafari && isNew && graphRef.current) {
        reorderCells(graphRef.current);
      } else if (!isSafari && isNew) {
        const sourceCellId = edge.getSourceCellId()
        const targetCellId = edge.getTargetCellId()
        const sourceCell = graphRef.current?.getCellById(sourceCellId);
        const targetCell = graphRef.current?.getCellById(targetCellId);

        sourceCell?.toFront();
        targetCell?.toFront()
        if (['loop', 'iteration'].includes(sourceCell?.getData()?.type)) {
          graphRef.current?.getEdges().forEach(edge => {
            const edgeSourceCell = graphRef.current?.getCellById(edge.getSourceCellId());
            const edgeTargetCell = graphRef.current?.getCellById(edge.getTargetCellId());
            if (edgeSourceCell?.getData()?.cycle === sourceCellId || edgeTargetCell?.getData()?.cycle === sourceCellId) {
              edge.toFront();
            }
          });
          graphRef.current?.getNodes().forEach(node => {
            if (node.getData()?.cycle === sourceCellId) node.toFront();
          });
        }
        if (['loop', 'iteration'].includes(targetCell?.getData()?.type)) {
          graphRef.current?.getEdges().forEach(edge => {
            const edgeSourceCell = graphRef.current?.getCellById(edge.getSourceCellId());
            const edgeTargetCell = graphRef.current?.getCellById(edge.getTargetCellId());
            if (edgeSourceCell?.getData()?.cycle === targetCellId || edgeTargetCell?.getData()?.cycle === targetCellId) {
              edge.toFront();
            }
          });
          graphRef.current?.getNodes().forEach(node => {
            if (node.getData()?.cycle === targetCellId) node.toFront();
          });
        }
      }
    });

    // During edge dragging, manually detect port hover since the dragging edge blocks mouse events
    let lastHoveredPort: { node: Node; portId: string } | null = null;
    graphRef.current.on('edge:mousemove', ({ e }: { e: MouseEvent }) => {
      if (!graphRef.current) return;
      const { clientX, clientY } = e;
      let found: { node: Node; portId: string } | null = null;

      for (const node of graphRef.current.getNodes()) {
        for (const port of node.getPorts().filter(p => p.group === 'right')) {
          const portView = graphRef.current.findViewByCell(node);
          if (!portView) continue;
          const portEl = (portView as any).findPortElem(port.id!, 'body') as SVGElement | null;
          if (!portEl) continue;
          const rect = portEl.getBoundingClientRect();
          const hitRadius = 16;
          const cx = rect.left + rect.width / 2;
          const cy = rect.top + rect.height / 2;
          if (Math.abs(clientX - cx) <= hitRadius && Math.abs(clientY - cy) <= hitRadius) {
            found = { node, portId: port.id! };
            break;
          }
        }
        if (found) break;
      }

      lastHoveredPort = found;
    });
    graphRef.current.on('edge:mouseup', () => { lastHoveredPort = null; });
    // Listen to copy keyboard event
    graphRef.current.bindKey(['ctrl+c', 'cmd+c'], copyEvent);
    // Listen to paste keyboard event
    graphRef.current.bindKey(['ctrl+v', 'cmd+v'], parseEvent);
    // Delete selected nodes and edges
    graphRef.current.bindKey(['ctrl+d', 'cmd+d', 'delete', 'backspace'], deleteEvent);
    // Undo / Redo
    graphRef.current.bindKey(['ctrl+z', 'cmd+z'], () => { undo(); return false; });
    graphRef.current.bindKey(['ctrl+y', 'cmd+y', 'ctrl+shift+z', 'cmd+shift+z'], () => { redo(); return false; });

  };

  return init
}
