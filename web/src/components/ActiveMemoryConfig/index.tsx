/**
 * ActiveMemoryConfig Component
 * 展示当前激活的记忆配置信息
 * 通过 props 接收 activeMemoryConfig 数据，支持自定义容器样式
 */
import { type FC } from 'react'
import { Flex } from 'antd'
import { Trans, useTranslation } from 'react-i18next'
import clsx from 'clsx';

import type { Memory } from '@/views/MemoryManagement/types'
import Tag from '@/components/Tag'

interface ActiveMemoryConfigProps {
  /** 当前激活的记忆配置数据 */
  activeMemoryConfig: Memory | null | undefined;
  variant?: 'outline' | 'filled';
  size?: 'default' | 'small';
  className?: string;
}

const ActiveMemoryConfig: FC<ActiveMemoryConfigProps> = ({
  activeMemoryConfig,
  variant = 'outline',
  size = 'default',
  className,
}) => {
  const { t } = useTranslation()

  if (!activeMemoryConfig) return null

  return (
    <div className={clsx({
      'rb:px-3 rb:rounded-lg': size === 'default',
      'rb:py-2 rb:px-3 rb:rounded-lg': size === 'small',
      'rb-border rb:bg-white rb:p-3': variant === 'outline',
      'rb:bg-[#F5F5F5] rb:p-3!': variant === 'filled',
    }, className)}>
      <Flex align="center" gap={12}
        className={clsx({
          'rb:text-[12px]': size === 'small'
        })}
      >
        <span className="rb:font-medium">{activeMemoryConfig?.config_name}</span>
        {activeMemoryConfig?.is_system_default
          ? <Tag color="dark"
            className={clsx({
              'rb:text-[10px]': size === 'small'
            })}
          >{t('memory.systemDefaultTag')}</Tag>
          : <Tag color="success"
            className={clsx({
              'rb:text-[10px]': size === 'small'
            })}
          >{t('memory.onlineActiveTag')}</Tag>
        }
        <span className="rb:text-[12px] rb:text-[#5B6167]">
          {t('memory.scene')}: {activeMemoryConfig?.scene_name || t('memory.defaultScene')}
        </span>
      </Flex>
      {activeMemoryConfig?.is_system_default
        ? <div className="rb:mt-2 rb:text-[12px] rb:text-[#5B6167]">
          <Trans
            i18nKey="application.systemDefaultDesc"
            components={{
              bold: <b />,
              memoryLink: <a href="#/memory" />,
            }}
          />
        </div>
        : activeMemoryConfig?.config_desc
        ? <div className="rb:mt-2 rb:text-[12px] rb:text-[#5B6167]">
          {activeMemoryConfig?.config_desc}
        </div>
        : null
      }
    </div>
  )
}

export default ActiveMemoryConfig