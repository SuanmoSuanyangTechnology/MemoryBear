import { forwardRef, useRef, useImperativeHandle, useState } from 'react';
import clsx from 'clsx';

import NodeLibrary from './components/NodeLibrary'
import Properties from './components/Properties';
import CanvasToolbar from './components/CanvasToolbar';
import { useWorkflowGraph } from './hooks/useWorkflowGraph';
import type { WorkflowRef } from '@/views/ApplicationConfig/types'
import Chat from './components/Chat/Chat';
import type { ChatRef } from './types'
import arrowIcon from '@/assets/images/workflow/arrow.png'

const Workflow = forwardRef<WorkflowRef>((_props, ref) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const miniMapRef = useRef<HTMLDivElement>(null);
  const chatRef = useRef<ChatRef>(null)
  const [collapsed, setCollapsed] = useState(false)
  // 使用自定义Hook初始化工作流图
  const {
    config,
    graphRef,
    selectedNode,
    setSelectedNode,
    zoomLevel,
    canUndo,
    canRedo,
    isHandMode,
    setIsHandMode,
    onUndo,
    onRedo,
    onDrop,
    blankClick,
    deleteEvent,
    copyEvent,
    parseEvent,
    handleSave
  } = useWorkflowGraph({ containerRef, miniMapRef });

  const onDragOver = (event: React.DragEvent) => {
    event.preventDefault();
  };
  const handleRun = () => {
    chatRef.current?.handleOpen()
  }
  const handleToggle = () => {
    setCollapsed(prev => !prev)
  }

  useImperativeHandle(ref, () => ({
    handleSave,
    handleRun,
    graphRef
  }))
  return (
    <div className="rb:h-[calc(100vh-64px)] rb:relative">
      {/* 左侧节点面板 */}
      {!collapsed  && <NodeLibrary />}
      <img 
        src={arrowIcon} 
        className={clsx('rb:cursor-pointer rb:w-5 rb:h-10 rb:absolute rb:top-[50%] rb:z-100', {
          'rb:left-0 rb:rotate-180': collapsed,
          'rb:left-80': !collapsed
        })} 
        onClick={handleToggle}
      />
      
      {/* 右侧画布区域 */}
      <div 
        className={clsx(`rb:fixed rb:top-16 rb:bottom-0 rb:right-75 rb:flex-1 rb:border-x rb:border-[#DFE4ED] rb:transition-all`, {
          'rb:left-80': !collapsed,
          'rb:left-0': collapsed
        })}
        onDrop={onDrop}
        onDragOver={onDragOver}
      >
        <div ref={containerRef} className="rb:w-full rb:h-full" />
        {/* 地图工具栏 */}
        <CanvasToolbar
          miniMapRef={miniMapRef}
          graphRef={graphRef}
          isHandMode={isHandMode}
          setIsHandMode={setIsHandMode}
          zoomLevel={zoomLevel}
          canUndo={canUndo}
          canRedo={canRedo}
          onUndo={onUndo}
          onRedo={onRedo}
        />
      </div>
      
      {/* 右侧属性面板 */}
      <Properties 
        selectedNode={selectedNode} 
        setSelectedNode={setSelectedNode}
        graphRef={graphRef}
        blankClick={blankClick}
        deleteEvent={deleteEvent}
        copyEvent={copyEvent}
        parseEvent={parseEvent}
      />
      <Chat
        ref={chatRef}
        graphRef={graphRef}
        appId={config?.app_id as string}
      />
    </div>
  );
});

export default Workflow;