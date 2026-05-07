import clsx from 'clsx';
import type { ReactShapeConfig } from '@antv/x6-react-shape';
import { Flex } from 'antd';
import { CheckCircleFilled, CloseCircleFilled, LoadingOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next'

import NodeTools from './NodeTools'

const LoopNode: ReactShapeConfig['component'] = ({ node }) => {
  const data = node.getData() || {};
  const { t } = useTranslation()

  return (
    <div className={clsx('rb:cursor-pointer rb:group rb:relative rb:h-full rb:w-full rb:p-3 rb:border rb:rounded-2xl rb:bg-[#FCFCFD] rb:shadow-[0px_2px_4px_0px_rgba(23,23,25,0.03)]', {
      'rb:border-[#171719]!': data.isSelected && !data.executionStatus,
      'rb:border-[#FCFCFD]': !data.isSelected && !data.executionStatus,
      'rb:border-[#369F21]!': !data.isSelected && data.executionStatus === 'completed',
      'rb:border-[#FF5D34]!': !data.isSelected && data.executionStatus === 'failed',
    })}>
      <NodeTools node={node} />
      <Flex align="center" gap={8} className="rb:flex-1">
        <div className={`rb:size-6 rb:bg-cover ${data.icon}`} />
        <div className="rb:wrap-break-word rb:line-clamp-1 rb:flex-1">{data.name ?? t(`workflow.${data.type}`)}</div>
        {data.executionStatus === 'completed'
          ? <CheckCircleFilled style={{ color: '#369F21', fontSize: 16 }} />
          : data.executionStatus === 'failed'
            ? <CloseCircleFilled style={{ color: '#FF5D34', fontSize: 16 }} />
            : data.executionStatus === 'running'
              ? <LoadingOutlined style={{ color: '#5B6167', fontSize: 16 }} />
              : null
        }
      </Flex>
      <div className="rb:mt-3 rb:min-h-[calc(100%-36px)] rb:w-full rb:bg-[radial-gradient(circle,#939AB1_1px,#F0F3F8_1px)] rb:shadow-[0px_2px_4px_0px_rgba(23,23,25,0.03)] rb:rounded-[10px] rb:bg-size-[12px_12px]"></div>
    </div>
  );
};

export default LoopNode;
