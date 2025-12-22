import type { FC } from 'react';
import { Select, Button } from 'antd';
import { Node } from '@antv/x6';
import type { GraphRef } from '../types'

interface CanvasToolbarProps {
  miniMapRef: React.RefObject<HTMLDivElement>;
  graphRef: GraphRef;
  isHandMode: boolean;
  setIsHandMode: React.Dispatch<React.SetStateAction<boolean>>;
  zoomLevel: number;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
}

const CanvasToolbar: FC<CanvasToolbarProps> = ({
  miniMapRef,
  graphRef,
  isHandMode,
  setIsHandMode,
  zoomLevel,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
}) => {
  // 整理布局函数
  const handleLayout = () => {
    if (!graphRef.current) return;
    const nodes = graphRef.current.getNodes();
    const edges = graphRef.current.getEdges();
    
    // 如果没有连线，使用垂直布局避免节点重叠
     if (edges.length === 0) {
       nodes.forEach((node, index) => {
         const nodeData = node.getData();
         const isSpecialNode = nodeData?.isGroup || nodeData?.type === 'condition';
         const nodeHeight = isSpecialNode ? 220 : 50;
         const xPosition = 100;
         const yPosition = index * (nodeHeight + 100) + 100;
         node.setPosition(xPosition, yPosition);
       });
       return;
     }
    
    // 简单的树布局算法
    const nodeMap = new Map<string, Node>();
    const children = new Map<string, string[]>();
    const roots: string[] = [];
    
    // 初始化节点映射
    nodes.forEach(node => {
      nodeMap.set(node.id, node);
      children.set(node.id, []);
    });
    
    // 构建父子关系
    edges.forEach(edge => {
      const sourceId = edge.getSourceCellId();
      const targetId = edge.getTargetCellId();
      if (sourceId && targetId) {
        children.get(sourceId)?.push(targetId);
      }
    });
    
    // 找到根节点
    const hasParent = new Set<string>();
    edges.forEach(edge => {
      const targetId = edge.getTargetCellId();
      if (targetId) hasParent.add(targetId);
    });
    
    nodes.forEach(node => {
      if (!hasParent.has(node.id)) {
        roots.push(node.id);
      }
    });
    
    // 布局参数
    const levelWidths: number[] = [];
    const baseNodeSpacing = 120;
    let currentY = 100;
    
    // 计算每层的最大宽度
    const calculateLevelWidths = (nodeId: string, level: number) => {
      const node = nodeMap.get(nodeId);
      if (!node) return;
      
      const nodeData = node.getData();
      const isSpecialNode = nodeData?.isGroup || nodeData?.type === 'condition';
      const nodeWidth = isSpecialNode ? 400 : 160;
      const gap = isSpecialNode ? 150 : 100;
      
      levelWidths[level] = Math.max(levelWidths[level] || 0, nodeWidth + gap);
      
      const childIds = children.get(nodeId) || [];
      childIds.forEach((childId: string) => calculateLevelWidths(childId, level + 1));
    };
    
    roots.forEach(rootId => calculateLevelWidths(rootId, 0));
    
    // 递归布局函数
    const layoutNode = (nodeId: string, level: number, parentY: number): number => {
      const node = nodeMap.get(nodeId);
      if (!node) return parentY;
      
      const nodeData = node.getData();
      const isSpecialNode = nodeData?.isGroup || nodeData?.type === 'condition';
      const nodeHeight = isSpecialNode ? 220 : 50;
      const verticalGap = isSpecialNode ? 80 : 40;
      const spacing = baseNodeSpacing + nodeHeight + verticalGap;
      
      const xPosition = levelWidths.slice(0, level).reduce((sum, width) => sum + width, 100);
      
      const childIds = children.get(nodeId) || [];
      
      if (childIds.length === 0) {
        // 叶子节点
        node.setPosition(xPosition, currentY);
        currentY += spacing;
        return currentY - spacing;
      } else {
        // 非叶子节点，先布局子节点
        const childPositions: number[] = [];
        childIds.forEach((childId: string) => {
          const childY = layoutNode(childId, level + 1, currentY);
          childPositions.push(childY);
        });
        
        // 父节点居中，确保有足够间隙
        const minY = Math.min(...childPositions);
        const maxY = Math.max(...childPositions);
        const centerY = (minY + maxY) / 2;
        node.setPosition(xPosition, centerY);
        return centerY;
      }
    };
    
    // 布局所有根节点
    roots.forEach(rootId => {
      layoutNode(rootId, 0, currentY);
      currentY += 300; // 不同树之间的间距
    });
  };

  return (
    <>
      {/* 小地图 */}
      <div ref={miniMapRef} className="rb:absolute rb:bottom-17 rb:left-5 rb:z-1000"></div>
      {/* 缩放控制按钮 */}
      <div className="rb:absolute rb:bottom-5 rb:left-5 rb:flex rb:flex-row rb:gap-2 rb:z-1000">
        <Button 
          type={isHandMode ? 'primary' : 'default'}
          onClick={() => {
            const newHandMode = !isHandMode;
            setIsHandMode(newHandMode);
            if (newHandMode) {
              graphRef.current?.enablePanning();
            } else {
              graphRef.current?.disablePanning();
            }
          }}
        >
          {isHandMode ? '✋' : '👆'}
        </Button>
        <Button onClick={() => graphRef.current?.zoom(0.1)}>+</Button>
        <Select
          value={Math.round(zoomLevel * 100)}
          onChange={(value: number | string) => {
            if (value === 'fit') {
              graphRef.current?.zoomToFit({ padding: 20 });
            } else {
              graphRef.current?.zoomTo((value as number) / 100);
            }
          }}
          labelRender={(props) => {
            console.log('props', props)
            return `${props.value}%`
          }}
          className="rb:w-20"
          options={[
            { label: '25%', value: 25 },
            { label: '50%', value: 50 },
            { label: '75%', value: 75 },
            { label: '100%', value: 100 },
            { label: '125%', value: 125 },
            { label: '150%', value: 150 },
            { label: '200%', value: 200 },
            { label: '自适应', value: 'fit' },
          ]}
        />
        <Button onClick={() => graphRef.current?.zoom(-0.1)}>-</Button>
        <Button disabled={!canUndo} onClick={onUndo}>撤销</Button>
        <Button disabled={!canRedo} onClick={onRedo}>重做</Button>
        <Button onClick={handleLayout}>整理</Button>
      </div>
    </>
  );
};

export default CanvasToolbar;
