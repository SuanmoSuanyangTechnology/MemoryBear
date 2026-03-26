import type { FC } from 'react';
import { Select, Divider } from 'antd';
import { PlusOutlined, MinusOutlined, FileAddOutlined } from '@ant-design/icons'
import clsx from 'clsx'
import { Node } from '@antv/x6';

import type { GraphRef } from '../types'

interface CanvasToolbarProps {
  /** Currently selected node */
  selectedNode: Node | null;
  miniMapRef: React.RefObject<HTMLDivElement>;
  graphRef: GraphRef;
  isHandMode: boolean;
  setIsHandMode: React.Dispatch<React.SetStateAction<boolean>>;
  zoomLevel: number;
  addNotes: () => void;
}

const CanvasToolbar: FC<CanvasToolbarProps> = ({
  selectedNode,
  miniMapRef,
  graphRef,
  zoomLevel,
  // canUndo,
  // canRedo,
  // onUndo,
  // onRedo,
  addNotes,
}) => {
  return (
    <>
      {/* 小地图 */}
      <div ref={miniMapRef} className={clsx("rb:absolute rb:bottom-15  rb:z-1000 rb:rounded-lg rb:overflow-hidden", {
        'rb:right-8': !selectedNode,
        'rb:right-95.5': selectedNode,
      })}></div>
      {/* 缩放控制按钮 */}
      <div className={clsx("rb:h-8.5 rb:bg-[#FFFFFF] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:shadow-[0px_2px_6px_0px_rgba(33,35,50,0.15)] rb:px-3 rb:py-2 rb:absolute rb:bottom-5 rb:flex rb:flex-row rb:items-center rb:gap-4 rb:z-1000", {
        'rb:right-8': !selectedNode,
        'rb:right-95.5': selectedNode,
      })}>
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
            console.log('props', props)
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
            { label: '自适应', value: 'fit' },
          ]}
          variant='borderless'
          size="small"
        />
        <PlusOutlined className="rb:text-[16px] rb:cursor-pointer" onClick={() => graphRef.current?.zoom(0.1)} />
        <Divider type="vertical" className="rb:h-4" />
        <FileAddOutlined onClick={addNotes} />
      </div>
    </>
  );
};

export default CanvasToolbar;
