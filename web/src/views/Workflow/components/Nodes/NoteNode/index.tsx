import { useRef, useState } from 'react';
import type { ReactShapeConfig } from '@antv/x6-react-shape';
import { Flex } from 'antd';

import NoteEditor from './NoteEditor';
import NoteNodeToolbar from './NoteNodeToolbar';
import { THEME_MAP } from '../../../constant'

const MIN_W = 240;
const MIN_H = 120;

const NoteNode: ReactShapeConfig['component'] = ({ node }) => {
  const data = node?.getData() || {};
  const nodeId = node?.id || '';
  const startRef = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  const [toolConfig, setToolConfig] = useState({
    fontSize: 12,
    bold: false,
    italic: false,
    strikethrough: false,
    list: false,
  })

  const handleFormat = (type: string, value?: unknown) => {
    console.log('handleFormat', type, value)
    if (type === 'color') {
      node?.setData({
        ...data,
        config: {
          ...data.config,
          theme: {
            ...data.config.theme,
            defaultValue: value
          }
        }
      });
    } else if (type === 'fontSize') {
      window.dispatchEvent(new CustomEvent('note:format', { detail: { id: nodeId, format: 'fontSize', value } }));
    } else if (type === 'link') {
      window.dispatchEvent(new CustomEvent('note:format', { detail: { id: nodeId, format: 'link', value: value || null } }));
    } else if (type === 'list') {
      window.dispatchEvent(new CustomEvent('note:format', { detail: { id: nodeId, format: 'list', value: !toolConfig.list } }));
    } else {
      window.dispatchEvent(new CustomEvent('note:format', { detail: { id: nodeId, format: type } }));
    }

    setToolConfig(prev => ({ ...prev, [type]: value || !prev[type as unknown as keyof typeof toolConfig]  }))
  };

  const onResizeMouseDown = (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    const size = node?.getSize();
    if (!size) return;
    startRef.current = { x: e.clientX, y: e.clientY, w: size.width, h: size.height };

    const onMouseMove = (ev: MouseEvent) => {
      if (!startRef.current) return;
      const w = Math.max(MIN_W, startRef.current.w + ev.clientX - startRef.current.x);
      const h = Math.max(MIN_H, startRef.current.h + ev.clientY - startRef.current.y);

      node?.setData({
        ...data,
        config: {
          ...data.config,
          width: {
            ...data.config.width,
            defaultValue: w
          },
          height: {
            ...data.config.height,
            defaultValue: h
          }
        }
      });
      node?.prop('size', { width: w, height: h });
    };
    const onMouseUp = () => {
      startRef.current = null;
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
  };

  const updateText = (value: string) => {
    node.setData({
      ...data,
      config: {
        ...data.config,
        text: {
          ...data.config.text,
          defaultValue: value
        }
      }
    })
  }

  const theme = THEME_MAP[data.config?.theme?.defaultValue || 'blue'] || THEME_MAP['blue']

  return (
    <div
      className="rb:relative rb:h-full rb:w-full rb:rounded-2xl rb:border"
      style={{
        background: theme.bg,
        borderColor: data.isSelected ? theme.outer : theme.border,
      }}
    >
      <div className="rb:h-4 rb:rounded-tl-2xl rb:rounded-tr-2xl"
        style={{
          background: theme.title
        }}
      ></div>
      {data.isSelected && <NoteNodeToolbar node={node!} nodeId={nodeId} toolConfig={toolConfig} onFormat={handleFormat} />}

      <div
        className="rb:w-full rb:h-[calc(100%-36px)] rb:p-2.5 rb:overflow-auto"
        onMouseDown={e => {
          e.stopPropagation()
          node?.setData({ ...node.getData(), isSelected: true })
        }}
        onWheel={e => e.stopPropagation()}
      >
        <NoteEditor
          nodeId={nodeId}
          value={data.config.text.defaultValue || ''}
          fontSize={toolConfig.fontSize}
          onChange={updateText}
          onFormatChange={(state) => setToolConfig(prev => ({ ...prev, ...state }))}
        />
      </div>
      <Flex align="center" justify="space-between" className="rb:pl-2.5! rb:pr-1!">
        <div className="rb:text-[12px] rb:text-[#5B6167]">
          {data.config.show_author.defaultValue
            ? data.config.author.defaultValue
            : undefined
          }
        </div>
        

        {/* <div className="rb:size-4 rb:border-b-[4px] rb:border-r-[4px]  rb:border-[#EBEBEB] rb:rounded-2xl"></div> */}
        <div
          onMouseDown={onResizeMouseDown}
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18" fill="none">
            <path fillRule="evenodd" clipRule="evenodd" d="M12 9.75V6H13.5V9.75C13.5 11.8211 11.8211 13.5 9.75 13.5H6V12H9.75C10.9926 12 12 10.9926 12 9.75Z" fill="black" fillOpacity="0.16"></path>
          </svg>
        </div>
      </Flex>
    </div>
  );
};

export default NoteNode;
