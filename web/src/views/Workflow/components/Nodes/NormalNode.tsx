import clsx from 'clsx';
import { useTranslation } from 'react-i18next'
import type { ReactShapeConfig } from '@antv/x6-react-shape';
import { Flex } from 'antd';
import { CheckCircleFilled, CloseCircleFilled, LoadingOutlined } from '@ant-design/icons';

import NodeTools from './NodeTools'

const NormalNode: ReactShapeConfig['component'] = ({ node }) => {
  const data = node?.getData() || {}
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
        <div className="rb:wrap-break-word rb:line-clamp-1 rb:flex-1 rb:text-[12px]">{data.name ?? t(`workflow.${data.type}`)}</div>
        {data.executionStatus === 'completed'
          ? <CheckCircleFilled style={{ color: '#369F21', fontSize: 16 }} />
          : data.executionStatus === 'failed'
            ? <CloseCircleFilled style={{ color: '#FF5D34', fontSize: 16 }} />
            : data.executionStatus === 'running'
              ? <LoadingOutlined style={{ color: '#5B6167', fontSize: 16 }} />
              : null
        }
      </Flex>

      {['memory-read', 'memory-write'].includes(data.type) && data.activeMemoryConfig
        ? <Flex align="center" gap={4}
            className="rb:bg-[#F0F3F8] rb:shadow-[0px_2px_4px_0px_rgba(23,23,25,0.03)] rb:rounded-md rb:px-1.5! rb:py-1! rb:text-[10px] rb:text-[#5B6167] rb:font-medium rb:leading-4 rb:mt-3! rb:truncate"
          >
            <div className="rb:size-3 rb:bg-cover rb:bg-[url('@/assets/images/conversation/memoryFunction.svg')] rb:shrink-0" />
            <span className="rb:truncate">{t('application.memoryConfiguration')}: {data.activeMemoryConfig.config_name}</span>
          </Flex>
        : <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4 rb:mt-3">{t('workflow.clickToConfigure')}</div>
      }
    </div>
  );
};

export default NormalNode;