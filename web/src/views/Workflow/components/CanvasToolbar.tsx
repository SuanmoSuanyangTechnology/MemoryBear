import type { FC } from 'react';
import { Select } from 'antd';
// import { Node } from '@antv/x6';
import type { GraphRef } from '../types'

interface CanvasToolbarProps {
  miniMapRef: React.RefObject<HTMLDivElement>;
  graphRef: GraphRef;
  isHandMode: boolean;
  setIsHandMode: React.Dispatch<React.SetStateAction<boolean>>;
  zoomLevel: number;
}

const CanvasToolbar: FC<CanvasToolbarProps> = ({
  miniMapRef,
  graphRef,
  zoomLevel,
}) => {
  return (
    <>
      {/* 小地图 */}
      <div ref={miniMapRef} className="rb:absolute rb:bottom-15  rb:right-8 rb:z-1000 rb:rounded-lg rb:overflow-hidden"></div>
      {/* 缩放控制按钮 */}
      <div className="rb:h-8.5 rb:bg-[#FFFFFF] rb-border rb:rounded-lg rb:shadow-[0px_2px_6px_0px_rgba(33,35,50,0.15)] rb:px-3 rb:py-2.25 rb:absolute rb:bottom-5 rb:right-8 rb:flex rb:flex-row rb:gap-2 rb:z-1000">
        <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/workflow/minus.png')]" onClick={() => graphRef.current?.zoom(-0.1)}></div>
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
        <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/workflow/plus.png')]" onClick={() => graphRef.current?.zoom(0.1)}></div>
      </div>
    </>
  );
};

export default CanvasToolbar;
