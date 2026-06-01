import { useState, forwardRef, useImperativeHandle } from 'react';
import { Select, Divider, Tooltip } from 'antd';
import { PlusOutlined, MinusOutlined, FileAddOutlined } from '@ant-design/icons'
import clsx from 'clsx'
import { Node } from '@antv/x6';
import { useTranslation } from 'react-i18next'

import type { GraphRef, WorkflowConfig } from '../types'
import VariableInspector from './VariableInspector'

interface CanvasToolbarProps {
  selectedNode: Node | null;
  miniMapRef: React.RefObject<HTMLDivElement>;
  graphRef: GraphRef;
  isHandMode: boolean;
  setIsHandMode: React.Dispatch<React.SetStateAction<boolean>>;
  zoomLevel: number;
  addNotes: () => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  lastExecuteId: string;
  config: WorkflowConfig | null;
  collapsed: boolean;
}
export interface CanvasToolbarRef {
  setIsVariableInspectorVisible: (visible: boolean) => void;
}

const CanvasToolbar = forwardRef<CanvasToolbarRef, CanvasToolbarProps>(({
  selectedNode,
  miniMapRef,
  graphRef,
  zoomLevel,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
  addNotes,
  isHandMode,
  setIsHandMode,
  lastExecuteId,
  config,
  collapsed,
}, ref) => {
  const { t } = useTranslation()
  const [isVariableInspectorVisible, setIsVariableInspectorVisible] = useState(false)

  useImperativeHandle(ref, () => ({
    setIsVariableInspectorVisible,
  }))

  return (
    <>
      <div
        className={clsx("rb:cursor-pointer rb:absolute rb:bottom-5 rb:h-8.5 rb:bg-[#FFFFFF] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:shadow-[0px_2px_6px_0px_rgba(33,35,50,0.15)] rb:px-3 rb:py-2 rb:text-[12px]", {
          'rb:bottom-5': !isVariableInspectorVisible,
          'rb:bottom-88': isVariableInspectorVisible,
          'rb:left-73': !collapsed,
          'rb:left-22': collapsed,
        })}
        onClick={() => setIsVariableInspectorVisible(prev => !prev)}
      >
        {t('workflow.variableInspector')}
      </div>
      <div>
        {/* 小地图 */}
        <div
          ref={miniMapRef}
          className={clsx("rb:absolute rb:z-1000 rb:rounded-lg rb:overflow-hidden", {
            'rb:bottom-15': !isVariableInspectorVisible,
            'rb:bottom-98': isVariableInspectorVisible,
            'rb:right-8': !selectedNode,
            'rb:right-95.5': selectedNode,
          })}
        ></div>
        {/* 缩放控制按钮 */}
        <div className={clsx("rb:h-8.5 rb:bg-[#FFFFFF] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:shadow-[0px_2px_6px_0px_rgba(33,35,50,0.15)] rb:px-3 rb:py-2 rb:absolute rb:bottom-5 rb:flex rb:flex-row rb:items-center rb:gap-4 rb:z-1000", {
          'rb:right-8': !selectedNode,
          'rb:right-95.5': selectedNode,
          'rb:bottom-5': !isVariableInspectorVisible,
          'rb:bottom-88': isVariableInspectorVisible,
        })}>
          <Tooltip title={t('workflow.pointerMode')}>
            <div
              className={clsx("rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/workflow/pointer.svg')]", {
                'rb:opacity-50 rb:cursor-pointer': isHandMode
              })}
              onClick={() => setIsHandMode(false)}
            ></div>
          </Tooltip>
          <Tooltip title={t('workflow.handMode')}>
            <div
              className={clsx("rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/workflow/cursor.svg')]", {
                'rb:opacity-50 rb:cursor-pointer': !isHandMode
              })}
              onClick={() => setIsHandMode(true)}
            ></div>
          </Tooltip>
          <Divider type="vertical" className="rb:h-4" />
          <MinusOutlined className="rb:text-[16px] rb:cursor-pointer" onClick={() => graphRef.current?.zoom(-0.1)} />
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
              return `${props.value}%`
            }}
            className="rb:w-20 rb:h-4!"
            options={[
              { label: '25%', value: 25 },
              { label: '50%', value: 50 },
              { label: '75%', value: 75 },
              { label: '100%', value: 100 },
              { label: '125%', value: 125 },
              { label: '150%', value: 150 },
              { label: '200%', value: 200 },
              { label: t('workflow.fit'), value: 'fit' },
            ]}
            variant='borderless'
            size="small"
          />
          <PlusOutlined className="rb:text-[16px] rb:cursor-pointer" onClick={() => graphRef.current?.zoom(0.1)} />
          <Divider type="vertical" className="rb:h-4" />
          <Tooltip title={`${t('workflow.undo')} (Ctrl+Z)`}>
            <div
              className={clsx("rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/workflow/undo.svg')]", {
                'rb:opacity-50': !canUndo,
                'rb:cursor-pointer': canUndo
              })}
              onClick={onUndo}
            ></div>
          </Tooltip>
          <Tooltip title={`${t('workflow.redo')} (Ctrl+Y)`}>
            <div
              className={clsx("rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/workflow/undo.svg')] rb:scale-x-[-1]", {
                'rb:opacity-50': !canRedo,
                'rb:cursor-pointer': canRedo
              })}
              onClick={onRedo}
            ></div>
          </Tooltip>
          <Divider type="vertical" className="rb:h-4" />
          <FileAddOutlined onClick={addNotes} />
        </div>
      </div>

      {/* 变量检查面板 */}
      {isVariableInspectorVisible &&
        <VariableInspector 
          selectedNode={selectedNode} 
          lastExecuteId={lastExecuteId}
          config={config}
          onClose={() => setIsVariableInspectorVisible(false)}
          collapsed={collapsed}
        />
      }
    </>
  );
});

export default CanvasToolbar;