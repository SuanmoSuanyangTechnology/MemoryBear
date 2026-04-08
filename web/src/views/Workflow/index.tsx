import { forwardRef, useRef, useImperativeHandle, useState } from 'react';
import clsx from 'clsx';

import NodeLibrary from './components/NodeLibrary'
import Properties from './components/Properties';
import CanvasToolbar from './components/CanvasToolbar';
import PortClickHandler from './components/PortClickHandler';
import { useWorkflowGraph } from './hooks/useWorkflowGraph';
import type { WorkflowRef, FeaturesConfigForm, FeaturesConfigModalRef } from '@/views/ApplicationConfig/types'
import Chat from './components/Chat/Chat';
import type { ChatRef, AddChatVariableRef } from './types'
import AddChatVariable from './components/AddChatVariable';
import FeaturesConfigModal from '@/views/ApplicationConfig/components/FeaturesConfig/FeaturesConfigModal'

const Workflow = forwardRef<WorkflowRef, { onFeaturesLoad?: (features: FeaturesConfigForm | undefined) => void }>(({ onFeaturesLoad }, ref) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const miniMapRef = useRef<HTMLDivElement>(null);
  const addChatVariableRef = useRef<AddChatVariableRef>(null)
  const chatRef = useRef<ChatRef>(null)
  const [collapsed, setCollapsed] = useState(false)
  // 使用自定义Hook初始化工作流图
  const {
    config,
    graphRef,
    selectedNode,
    zoomLevel,
    isHandMode,
    setIsHandMode,
    onDrop,
    blankClick,
    deleteEvent,
    copyEvent,
    parseEvent,
    handleSave,
    chatVariables,
    setChatVariables,
    handleAddNotes,
    handleSaveFeaturesConfig,
    features,
    getStartNodeVariables,
  } = useWorkflowGraph({ containerRef, miniMapRef, onFeaturesLoad });

  const onDragOver = (event: React.DragEvent) => {
    event.preventDefault();
  };
  const handleRun = () => {
    chatRef.current?.handleOpen()
  }
  const handleToggle = () => {
    setCollapsed(prev => !prev)
  }
  const addVariable = () => {
    addChatVariableRef.current?.handleOpen()
  }

  // Ref used to imperatively open the config modal
  const funConfigModalRef = useRef<FeaturesConfigModalRef>(null)

  /** Open the feature config modal pre-populated with the current values */
  const handleFeaturesConfig = () => {
    blankClick()
    funConfigModalRef.current?.handleOpen(features as FeaturesConfigForm)
  }

  useImperativeHandle(ref, () => ({
    handleSave,
    handleRun,
    graphRef,
    addVariable,
    chatVariables,
    config,
    features: features,
    handleFeaturesConfig,
    handleSaveFeaturesConfig
  }))
  return (
    <div className="rb:h-full rb:relative">
      {/* 左侧节点面板 */}
      <NodeLibrary collapsed={collapsed} handleToggle={handleToggle} />
      
      {/* 右侧画布区域 */}
      <div 
        className={clsx(`rb:fixed rb:top-16 rb:bottom-0 rb:left-0 rb:right-0 rb:transition-all`)}
        onDrop={onDrop}
        onDragOver={onDragOver}
      >
        <div ref={containerRef} className="rb:w-full rb:h-full" />
        {/* 地图工具栏 */}
        <CanvasToolbar
          selectedNode={selectedNode}
          miniMapRef={miniMapRef}
          graphRef={graphRef}
          isHandMode={isHandMode}
          setIsHandMode={setIsHandMode}
          zoomLevel={zoomLevel}
          addNotes={handleAddNotes}
        />
      </div>
      
      {/* 右侧属性面板 */}
      {selectedNode &&
        <Properties 
          selectedNode={selectedNode}
          graphRef={graphRef}
          blankClick={blankClick}
          deleteEvent={deleteEvent}
          copyEvent={copyEvent}
          parseEvent={parseEvent}
          config={config}
          chatVariables={chatVariables}
        />
      }
      <Chat
        ref={chatRef}
        data={config}
        features={features}
        graphRef={graphRef}
        appId={config?.app_id as string}
      />
      <PortClickHandler graph={graphRef.current} />

      <AddChatVariable
        ref={addChatVariableRef}
        variables={chatVariables}
        onChange={setChatVariables}
      />
      {/* Modal for editing feature settings; calls refresh on save */}
      <FeaturesConfigModal
        ref={funConfigModalRef}
        refresh={handleSaveFeaturesConfig}
        source="workflow"
        chatVariables={getStartNodeVariables().map(v => ({ name: v.name, key: `start_${v.name}`, label: v.name, type: 'variable', dataType: v.type, value:`{{${v.name}}}` })) as any}
      />
    </div>
  );
});

export default Workflow;