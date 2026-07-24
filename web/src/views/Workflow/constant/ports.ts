import type { GroupMetadata, PortMetadata } from '@antv/x6/lib/model/port';

import { nodeWidth } from './layout';

/**
 * Port configuration interface
 */
export interface PortsConfig {
  /** Port group metadata */
  groups?: GroupMetadata;
  /** Port item metadata array */
  items?: PortMetadata[];
}

/**
 * Node configuration interface
 */
export interface NodeConfig {
  /** Node width in pixels */
  width: number;
  /** Node height in pixels */
  height: number;
  /** Node shape type */
  shape: string;
  /** Port configuration */
  ports?: PortsConfig;
}

/** Edge color for normal state */
export const edge_color = '#D4D5D9';
/** Edge color for selected state */
export const edge_selected_color = '#171719'
export const edge_width = 2;
/** Port color */
export const port_color = '#171719'
/**
 * Unified port markup configuration
 * Defines SVG elements for port rendering
 */
export const portMarkup = [
  {
    tagName: 'circle',
    selector: 'body',
  },
  {
    tagName: 'text',
    selector: 'label',
  },
];

/**
 * Unified port attributes configuration
 * Defines visual styling for ports
 */
export const portAttrs = {
  body: {
    r: 6, 
    magnet: true, 
    stroke: port_color, 
    strokeWidth: edge_width, 
    fill: port_color,
  },
  label: {
    text: '+',
    fontSize: 12,
    fontWeight: 'bold',
    fill: '#FFFFFF',
    textAnchor: 'middle',
    textVerticalAnchor: 'middle',
    pointerEvents: 'none',
  },
}
export const portTextAttrs = { fontSize: 12, fill: '#5B6167' }
/**
 * Port position arguments
 */
export const portItemArgsY = 26;
export const portArgs = { x: nodeWidth, y: portItemArgsY }

export const defaultPortGroup = {
  position: { name: 'absolute' },
  markup: [
    { tagName: 'rect', selector: 'body' },
    { tagName: 'circle', selector: 'hoverBody' },
    { tagName: 'text', selector: 'label' },
  ],
  attrs: {
    body: {
      width: 1,
      height: 8,
      x: 0.75,
      magnet: true,
      stroke: port_color,
      strokeWidth: edge_width,
      fill: port_color,
    },
    hoverBody: {
      r: 6,
      cy: 2,
      magnet: true,
      stroke: port_color,
      strokeWidth: edge_width,
      fill: port_color,
      opacity: 1,
    },
    label: {
      text: '+',
      fontSize: 12,
      fontWeight: 'bold',
      fill: '#FFFFFF',
      textAnchor: 'middle',
      textVerticalAnchor: 'middle',
      pointerEvents: 'none',
      y: '0.15em',
      opacity: 1,
    },
  },
}

export const leftPortGroup = {
  position: { name: 'absolute' },
  markup: [{ tagName: 'rect', selector: 'body' }],
  attrs: {
    body: {
      width: 1,
      height: 8,
      x: -1.75,
      y: -4,
      magnet: true,
      stroke: port_color,
      strokeWidth: edge_width,
      fill: port_color,
    },
  },
}

/**
 * Unified port group configuration
 * Defines port positions and attributes for different sides
 */
export const defaultAbsolutePortGroups = {
  right: defaultPortGroup,
  left: leftPortGroup,
}
/**
 * Default port items for standard nodes
 */
export const defaultPortItems = [
  { group: 'left', args: { x: 0, y: portItemArgsY }, },
  { group: 'right', args: { x: nodeWidth, y: portItemArgsY }, },
];
