/**
 * ActiveConfigBanner Component
 * Displays the currently active online configuration at the top of the page.
 * Shown when a user-created configuration (is_system_default = false) exists,
 * including its name, scenario, last updated timestamp and description.
 */
import { type FC, useState } from 'react'
import { Flex, Button, App } from 'antd'
import { useTranslation } from 'react-i18next'

import type { Memory } from '../types'
import { formatDateTime } from '@/utils/format'
import { validateMemoryConfig } from '@/api/memory'
import Tag from '@/components/Tag'

interface ActiveConfigBannerProps {
  config: Memory;
}

const ActiveConfigBanner: FC<ActiveConfigBannerProps> = ({ config }) => {
  const { t } = useTranslation()
  const { modal, message } = App.useApp()
  const [loading, setLoading] = useState(false)

  const handleValidateClick = () => {
    setLoading(true)
    validateMemoryConfig()
      .then((res) => {
        const { warnings = [], valid } = res as {
          valid: boolean;
          config_id: string;
          config_name: string;
          warnings: {
            model_type: string;
            model_id: string | null;
            message: string;
            source: string;
          }[];
        }
        if (valid) {
          message.success(t('memory.validateSuccess'))
        } else {
          modal.warning({
            title: t('memory.validateFailed'),
            content: warnings.map((item, index) => <div key={item.model_type}>{index + 1}. {item.message} ({t(`memory.${item.source}`)})</div>),
          })
        }
        setLoading(false)
      })
      .finally(() => {
        setLoading(false)
      })
  }

  return (
    <div className="rb:rb-border rb:rounded-xl rb:bg-white rb:px-5! rb:py-3! rb:mb-4">
      <Flex align="center" gap={12} className="rb:mb-3!">
        <Flex align="center" gap={8}>
          <span className="rb:size-1.5 rb:rounded-full rb:bg-[#369F21]"></span>
          <span className="rb:text-[14px] rb:font-medium rb:leading-5 rb:text-[#212332]">
            {t('memory.onlineActiveConfig')}
          </span>
        </Flex>
        <Flex align="center" gap={8}>
          <Tag color="success" circle={true}>
            {t('memory.activeStatus')}
          </Tag>
          <Button type="primary" size="small"
            className="rb:text-[12px]!"
            loading={loading}
            onClick={handleValidateClick}
          >
            {t('memory.validate')}
          </Button>
        </Flex>
      </Flex>

      <div className="rb:grid rb:grid-cols-3 rb:gap-x-6 rb:gap-y-3">
        <div>
          <div className="rb:text-[12px] rb:leading-4 rb:text-[#9A9A9A] rb:mb-1">{t('memory.configurationName')}</div>
          <div className="rb:text-[14px] rb:leading-5 rb:text-[#212332]">{config.config_name || '-'}</div>
        </div>
        <div>
          <div className="rb:text-[12px] rb:leading-4 rb:text-[#9A9A9A] rb:mb-1">{t('memory.scene')}</div>
          <div className="rb:text-[14px] rb:leading-5 rb:text-[#212332]">{config.scene_name || '-'}</div>
        </div>
        <div>
          <div className="rb:text-[12px] rb:leading-4 rb:text-[#9A9A9A] rb:mb-1">{t('memory.updateTime')}</div>
          <div className="rb:text-[14px] rb:leading-5 rb:text-[#212332]">
            {config.updated_at ? formatDateTime(config.updated_at, 'YYYY-MM-DD HH:mm:ss') : '-'}
          </div>
        </div>
        <div className="rb:col-span-3">
          <div className="rb:text-[12px] rb:leading-4 rb:text-[#9A9A9A] rb:mb-1">{t('memory.desc')}</div>
          <div className="rb:text-[14px] rb:leading-5 rb:text-[#212332]">{config.config_desc || '-'}</div>
        </div>
      </div>
    </div>
  )
}

export default ActiveConfigBanner
