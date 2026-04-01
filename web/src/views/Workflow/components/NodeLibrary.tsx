import { type FC } from 'react';
import { useTranslation } from 'react-i18next'
import { Flex, Tooltip } from 'antd'
import clsx from 'clsx';

import { nodeLibrary } from '../constant';
import RbCard from '@/components/RbCard/Card';

const NodeLibrary: FC<{ collapsed: boolean; handleToggle: () => void }> = ({ collapsed, handleToggle }) => {
  const { t } = useTranslation()

  return (
    <div className={clsx("rb:h-[calc(100vh-88px)] rb:overflow-hidden rb:fixed rb:left-2.5 rb:top-18.5 rb:z-1000", {
      'rb:w-65': !collapsed,
      'rb:w-14': collapsed
    })}>
      <RbCard
        title={collapsed ? undefined :t('workflow.nodeName')}
        extra={
          <div className={clsx("rb:cursor-pointer rb:size-5 rb:bg-cover rb:bg-[url('@/assets/images/workflow/menuFold.svg')]", {
            'rb:rotate-180 rb:mr-1': collapsed
          })} onClick={handleToggle}></div>
        }
        headerType="borderless"
        headerClassName={clsx("rb:font-[MiSans-Bold] rb:font-bold rb:text-[12px]!", {
          'rb:min-h-[42px]!': !collapsed,
          'rb:min-h-[52px]!': collapsed
        })}
        className="rb:h-full! rb:hover:shadow-none!"
        bodyClassName={clsx('rb:overflow-y-auto! rb:pt-0! rb:pb-3!', {
          'rb:px-0! rb:h-[calc(100%-52px)]!': collapsed,
          'rb:px-3! rb:h-[calc(100%-42px)]!': !collapsed
        })}
      >
        <Flex vertical align={collapsed ? 'center' : undefined} gap={collapsed ? 8 : 16}>
          {collapsed
            ? <>
              {nodeLibrary.map(category => (
                <>
                  {category.nodes
                    .filter(node => node.type !== 'cycle-start' && node.type !== 'break')
                    .map((node, nodeIndex) => (
                      <Tooltip key={nodeIndex} title={t(`workflow.${node.type}`)} placement="right">
                        <div
                          className="rb:p-2 rb:rounded-lg rb:hover:bg-[rgba(33,35,50,0.08)]"
                          draggable
                          onDragStart={(e) => {
                            e.dataTransfer.setData('application/reactflow', node.type);
                            e.dataTransfer.setData('application/json', JSON.stringify(node));
                          }}
                        >
                          <div className={`rb:size-6 rb:cursor-pointer rb:bg-cover ${node.icon}`} />
                        </div>
                      </Tooltip>
                    ))
                  }
                </>
              ))}
            </>
            : nodeLibrary.map(category => (
              <div
                key={category.category}
              >
                <div className="rb:font-semibold rb:mb-2 rb:text-[12px] rb:leading-4.5 rb:pl-1">{t(`workflow.${category.category}`)}</div>
                <Flex gap={6} vertical>
                  {category.nodes
                    .filter(node => node.type !== 'cycle-start' && node.type !== 'break')
                    .map((node, nodeIndex) => (
                      <Flex
                        key={nodeIndex}
                        align="center"
                        gap={8}
                        className="rb:rounded-xl rb:p-2! rb:border rb:border-[#EBEBEB] rb:cursor-pointer rb:hover:border rb:hover:border-[#171719]!"
                        draggable
                        onDragStart={(e) => {
                          e.dataTransfer.setData('application/reactflow', node.type);
                          e.dataTransfer.setData('application/json', JSON.stringify(node));
                        }}
                      >
                        <div className={`rb:size-6 rb:bg-cover ${node.icon}`} />
                        <span className="rb:font-medium rb:text-[12px] rb:leading-4">{t(`workflow.${node.type}`)}</span>
                      </Flex>
                    ))}
                </Flex>
              </div>
            ))
          }
        </Flex>
      </RbCard>
    </div>
  );
};

export default NodeLibrary;