/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-10 14:06:09 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-10 14:06:09 
 */
/**
 * GraphNetworkChart Component
 * 
 * A force-directed graph visualization component built with ECharts.
 * Displays nodes and edges in an interactive network diagram with physics-based layout.
 * Supports zooming, panning, dragging nodes, and click interactions.
 */
import { type FC, useEffect, useRef, type SetStateAction, type Dispatch } from 'react'
import ReactEcharts from 'echarts-for-react';

import PageEmpty from '@/components/Empty/PageEmpty'

// Default color palette for node categories
const Colors = ['#171719', '#155EEF', '#9C6FFF', '#FF8A4C']

/**
 * Node interface representing a graph node/vertex
 */
export interface Node {
  id: string;              // Unique identifier for the node
  label: string;           // Display label for the node
  category: number;        // Category index for grouping and coloring
  symbolSize: number;      // Size of the node symbol in pixels
  name: string;            // Node name (used in ECharts)
  itemStyle: {
    color: string;         // Custom color for this node
  }
  caption: string;         // Additional description or caption
  [key: string]: any;      // Allow additional custom properties
}

/**
 * Edge interface representing a connection between two nodes
 */
export interface Edge {
  id: string;              // Unique identifier for the edge
  source: string;          // Source node ID
  target: string;          // Target node ID
  type: string;            // Type/category of the relationship
  caption: string;         // Description of the relationship
  value: number;           // Numeric value associated with the edge
  weight: number;          // Weight/strength of the connection
}

/**
 * Props for the GraphNetworkChart component
 */
interface GraphNetworkChartProps {
  nodes: Node[];                                    // Array of nodes to display in the graph
  links: Edge[];                                    // Array of edges connecting the nodes
  categories: { name: string }[];                   // Category definitions for node grouping
  colors?: string[];                                // Optional custom color palette (defaults to Colors)
  onNodeClick: Dispatch<SetStateAction<Node | null>>;  // Callback when a node is clicked
}

const GraphNetworkChart: FC<GraphNetworkChartProps> = ({
  nodes,
  links,
  categories,
  colors = Colors,
  onNodeClick,
}) => {
  // Reference to the ECharts instance for programmatic control
  const chartRef = useRef<ReactEcharts>(null);
  
  // Flag to prevent multiple simultaneous resize operations (debouncing)
  const resizeScheduledRef = useRef(false)

  /**
   * Effect: Handle responsive chart resizing
   * 
   * Uses ResizeObserver to detect container size changes and resize the chart accordingly.
   * Implements requestAnimationFrame for smooth, debounced resize operations.
   * Re-runs when nodes change to ensure proper sizing with new data.
   */
  useEffect(() => {
    const handleResize = () => {
      if (chartRef.current && !resizeScheduledRef.current) {
        resizeScheduledRef.current = true
        // Use requestAnimationFrame for smooth, optimized resize
        requestAnimationFrame(() => {
          chartRef.current?.getEchartsInstance().resize();
          resizeScheduledRef.current = false
        });
      }
    }

    // Observe the chart container for size changes
    const resizeObserver = new ResizeObserver(handleResize)
    const chartElement = chartRef.current?.getEchartsInstance().getDom().parentElement
    if (chartElement) {
      resizeObserver.observe(chartElement)
    }

    // Cleanup: disconnect observer when component unmounts
    return () => {
      resizeObserver.disconnect()
    }
  }, [nodes])

  return (
    <div className="rb:w-full rb:h-full">
      {/* Render chart only if nodes exist, otherwise show empty state */}
      {nodes && nodes.length > 0
        ? <ReactEcharts
            ref={chartRef}
            option={{
              // Color palette for node categories
              colors: colors,
              
              // Disable default tooltip (custom interaction via onNodeClick)
              tooltip: {
                show: false
              },
              
              // Hide legend (categories not displayed in legend)
              legend: {
                show: false,
                bottom: 12,
              },
              
              series: [
                {
                  type: 'graph',              // Graph/network chart type
                  layout: 'force',            // Force-directed layout algorithm
                  data: nodes || [],          // Node data
                  links: links || [],         // Edge data
                  categories: categories,     // Category definitions
                  roam: true,                 // Enable zoom and pan interactions
                  
                  // Dynamic zoom level based on node count for better initial view
                  zoom: nodes.length < 50 ? 3 : nodes.length < 100 ? 2 : 1,
                  
                  // Node label configuration
                  label: {
                    show: true,               // Display labels
                    position: 'right',        // Position label to the right of node
                    formatter: '{b}',         // Use node name as label text
                  },
                  
                  // Edge styling
                  lineStyle: {
                    color: '#5B6167',         // Gray color for edges
                    curveness: 0.3            // Slight curve for better visibility
                  },
                  
                  // Force-directed layout physics configuration
                  force: {
                    repulsion: 100,           // Repulsion force between nodes
                    edgeLength: 80,           // Ideal distance between connected nodes
                    gravity: 0.3,             // Gravity pulling nodes to center
                    layoutAnimation: true,    // Animate layout changes
                    preventOverlap: true,     // Prevent nodes from overlapping
                    edgeSymbol: ['none', 'arrow'],  // Arrow on target end of edge
                    edgeSymbolSize: [4, 10],  // Size of edge symbols
                    initLayout: 'force'       // Use force-directed for initial layout
                  },
                  
                  selectedMode: 'single',     // Allow selecting one node at a time
                  draggable: true,            // Enable dragging nodes
                  animationDurationUpdate: 0, // Disable animation on data update for performance
                  
                  // Styling for selected nodes
                  select: {
                    itemStyle: {
                      borderWidth: 2,         // Thicker border when selected
                      borderColor: '#ffffff', // White border for contrast
                      shadowBlur: 10,         // Glow effect on selection
                    }
                  }
                }
              ]
            }}
            style={{ height: '100%', width: '100%' }}
            notMerge={false}      // Merge options instead of replacing (better performance)
            lazyUpdate={true}     // Batch updates for better performance
            
            // Event handlers
            onEvents={{
              click: (params: { dataType: string; data: Node; name: string }) => {
                // Only trigger callback for node clicks (not edges or background)
                if (params.dataType === 'node') {
                  onNodeClick(params.data)
                }
              }
            }}
          />
        : <PageEmpty />
      }
    </div>
  )
}

export default GraphNetworkChart
