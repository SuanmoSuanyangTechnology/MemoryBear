import { edge_color, edge_width, port_color } from './ports';

/**
 * Output variable configuration interface
 */
export interface OutputVariable {
  /** Default output variables */
  default?: Array<{
    name: string;
    type: string;
  }>;
  /** Dynamically defined variable keys */
  define?: string[];
  /** Error-related output variables */
  error?: Array<{
    name: string;
    type: string;
  }>;
}

/**
 * Default edge attributes configuration
 * Defines visual styling for edges/connections
 */
export const edgeAttrs = {
  attrs: {
    line: {
      stroke: edge_color,
      strokeWidth: edge_width,
      targetMarker: null,
      sourceMarker: null,
    },
  },
}

/**
 * Edge hover tool: circular "+" button shown at midpoint on hover
 */
export const edgeHoverTool = {
  name: 'button',
  args: {
    markup: [
      {
        tagName: 'circle',
        selector: 'button',
        attrs: {
          r: 6,
          stroke: port_color,
          strokeWidth: edge_width,
          fill: port_color,
          cursor: 'pointer',
        },
      },
      {
        tagName: 'text',
        textContent: '+',
        selector: 'icon',
        attrs: {
          fontSize: 12,
          fontWeight: 'bold',
          fill: '#FFFFFF',
          textAnchor: 'middle',
          textVerticalAnchor: 'middle',
          pointerEvents: 'none',
          y: '0.3em',
        },
      },
    ],
    distance: 0.5,
    offset: { x: 0, y: 0 },
    onClick({ e, cell: edge }: any) {
      e.stopPropagation();
      const graph = edge.model?.graph;
      if (!graph) return;
      const sourceCell = graph.getCellById(edge.getSourceCellId());
      const targetCell = graph.getCellById(edge.getTargetCellId());
      const sourcePort = edge.getSourcePortId();
      const targetPort = edge.getTargetPortId();
      if (!sourceCell || !targetCell) return;
      const rect = (e.target as HTMLElement).getBoundingClientRect();
      const tempDiv = document.createElement('div');
      tempDiv.style.position = 'fixed';
      tempDiv.style.left = rect.left + 'px';
      tempDiv.style.top = rect.top + 'px';
      tempDiv.style.width = '1px';
      tempDiv.style.height = '1px';
      tempDiv.style.zIndex = '9999';
      document.body.appendChild(tempDiv);
      window.dispatchEvent(new CustomEvent('port:click', {
        detail: {
          node: sourceCell,
          port: sourcePort,
          element: tempDiv,
          rect,
          edgeInsertion: { edge, sourceCell, targetCell, sourcePort, targetPort }
        }
      }));
    },
  },
}
