import clsx from 'clsx';
import { useTranslation } from 'react-i18next'
import type { ReactShapeConfig } from '@antv/x6-react-shape';
import { Flex } from 'antd';

import NodeTools from './NodeTools'

const NormalNode: ReactShapeConfig['component'] = ({ node }) => {
  const data = node?.getData() || {}
  const { t } = useTranslation()

  return (
    <div className={clsx('rb:cursor-pointer rb:group rb:relative rb:h-full rb:w-full rb:p-3 rb:border rb:rounded-2xl rb:bg-[#FCFCFD] rb:shadow-[0px_2px_4px_0px_rgba(23,23,25,0.03)]', {
      'rb:border-[#171719]': data.isSelected,
      'rb:border-[#FCFCFD]': !data.isSelected
    })}>
      <NodeTools node={node} />
      <Flex align="center" gap={8} className="rb:flex-1">
        <img src={data.icon} className="rb:size-6" />
        <div className="rb:wrap-break-word rb:line-clamp-1">{data.name ?? t(`workflow.${data.type}`)}</div>
      </Flex>

      <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4 rb:mt-3">{t('workflow.clickToConfigure')}</div>
    </div>
  );
};

export default NormalNode;