import { useTranslation } from 'react-i18next'
import clsx from 'clsx';
import type { ReactShapeConfig } from '@antv/x6-react-shape';
import { Flex } from 'antd';

import NodeTools from './NodeTools'

const caculateIsSet = (item: any, type: string) => {
  switch(type) {
    case 'categories':
      return typeof item?.class_name === 'string' && item?.class_name !== ''
    case 'cases':
      return item.expressions.length > 0 && item.expressions.filter((vo: any) => {
        const keys = Object.keys(vo)
        return keys.length === 0 || (keys.length > 0
          && ((['not_empty', 'empty'].includes(vo.operator) && (['undefined', 'null'].includes(typeof vo.left) || vo.left === ''))
          || (!['not_empty', 'empty'].includes(vo.operator) && (['undefined', 'null'].includes(typeof vo.right) || vo.right === ''))))
      }).length === 0
  }
}
const ConditionNode: ReactShapeConfig['component'] = ({ node }) => {
  const data = node?.getData() || {};
  const { t } = useTranslation()

  return (
    <div className={clsx('rb:cursor-pointer rb:group rb:relative rb:h-full rb:w-full rb:p-3 rb:border rb:rounded-2xl rb:bg-[#FCFCFD] rb:shadow-[0px_2px_4px_0px_rgba(23,23,25,0.03)]', {
      'rb:border-[#171719]': data.isSelected,
      'rb:border-[#DFE4ED]': !data.isSelected
    })}>
      <NodeTools node={node} />
      <Flex align="center" gap={8} className="rb:flex-1">
        <img src={data.icon} className="rb:size-6" />
        <div className="rb:wrap-break-word rb:line-clamp-1">{data.name ?? t(`workflow.${data.type}`)}</div>
      </Flex>

      {data.type === 'question-classifier' &&
        <Flex vertical gap={4} className="rb:mt-3!">
          {data.config?.categories?.defaultValue.map((item: any, index: number) => (
            <div key={index} className="rb:bg-[#F0F3F8] rb:shadow-[0px_2px_4px_0px_rgba(23,23,25,0.03)] rb:rounded-md rb:py-1 rb:px-1.5 rb:text-[10px] rb:text-[#5B6167] rb:font-medium rb:leading-3.5">
              <Flex justify="space-between">
                <span>{t('workflow.config.question-classifier.class_name')} {index + 1}</span>
                {caculateIsSet(item, 'categories') ? t(`workflow.config.${data.type}.set`) : t(`workflow.config.${data.type}.unset`)}
              </Flex>
            </div>
          ))}
        </Flex>
      }
      {data.type === 'if-else' &&
        <Flex vertical gap={4} className="rb:mt-3!">
          {data.config?.cases?.defaultValue.map((item: any, index: number) => (
            <div key={index} className="rb:bg-[#F0F3F8] rb:shadow-[0px_2px_4px_0px_rgba(23,23,25,0.03)] rb:rounded-md rb:py-1 rb:px-1.5 rb:text-[10px] rb:text-[#5B6167] rb:font-medium rb:leading-3.5">
              <Flex justify="space-between">
                <span>{index === 0 ? 'IF' : `ELIF`}</span>
                {caculateIsSet(item, 'cases') ? t(`workflow.config.${data.type}.set`) : t(`workflow.config.${data.type}.unset`)}
              </Flex>
            </div>
          ))}
          <div className="rb:bg-[#F0F3F8] rb:shadow-[0px_2px_4px_0px_rgba(23,23,25,0.03)] rb:rounded-md rb:py-1 rb:px-1.5 rb:text-[10px] rb:text-[#5B6167] rb:font-medium rb:leading-3.5">
            ELSE
          </div>
        </Flex>
      }
    </div>
  );
};

export default ConditionNode;