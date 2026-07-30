import type { Graph } from '@antv/x6';

/**
 * Assign explicit zIndex values to enforce layer order:
 *   parent nodes (loop/iteration) → child edges → child nodes
 * Ports live inside each node's SVG container and are always above
 * edges once the node zIndex is higher than the edge zIndex.
 */
export const reorderCells = (graph: Graph) => {
  // Safari uses x6-html-shape (dual HTML layer architecture).
  // zIndex controls order within each HTML layer and SVG layer.
  graph.getEdges().forEach(edge => edge.setZIndex(0));
  graph.getNodes().forEach(node => {
    node.setZIndex(node.getData()?.cycle ? 2 : 1);
  });
};
