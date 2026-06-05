import { type FC, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Flex, Tooltip, Switch } from 'antd'

import type { SubmitTypes, SubmitTypeEditModalRef } from './types'
// import type { EmailConfig, EmailConfigModalRef } from './EmailConfigModal'
import SubmitTypeEditModal from './SubmitTypeEditModal'
// import EmailConfigModal from './EmailConfigModal'

interface SubmitTypeListProps {
  value?: SubmitTypes;
  onChange?: (value: SubmitTypes) => void
}

const submitTypes = [
  {
    type: 'webapp',
    icon: 'rb:bg-[#1677FF] rb:rounded-md rb:size-4 rb:flex rb:items-center rb:justify-center rb:text-white rb:text-[10px]',
    iconText: 'W',
    description: '在 Web 应用中显示给最终用户'
  },
  {
    type: 'email',
    icon: 'rb:bg-[#722ED1] rb:rounded-md rb:size-4 rb:flex rb:items-center rb:justify-center rb:text-white rb:text-[10px]',
    iconText: 'E',
    description: '通过电子邮件发送输入请求',
    hasConfig: true
  },
  {
    type: 'slack',
    icon: 'rb:bg-[#E01E5A] rb:rounded-md rb:size-4 rb:flex rb:items-center rb:justify-center rb:text-white rb:text-[10px]',
    iconText: 'S',
    description: '通过 Slack 发送输入请求',
    disabled: true
  },
  {
    type: 'teams',
    icon: 'rb:bg-[#464EB8] rb:rounded-md rb:size-4 rb:flex rb:items-center rb:justify-center rb:text-white rb:text-[10px]',
    iconText: 'T',
    description: '通过 Teams 发送输入请求',
    disabled: true
  },
  {
    type: 'discord',
    icon: 'rb:bg-[#5865F2] rb:rounded-md rb:size-4 rb:flex rb:items-center rb:justify-center rb:text-white rb:text-[10px]',
    iconText: 'D',
    description: '通过 Discord 发送输入请求',
    disabled: true
  }
]

const SubmitTypeList: FC<SubmitTypeListProps> = ({
  value = {},
  onChange
}) => {
  const { t } = useTranslation()
  const submitTypeEditModalRef = useRef<SubmitTypeEditModalRef>(null)
  // const emailConfigModalRef = useRef<EmailConfigModalRef>(null)

  const handleAdd = () => {
    submitTypeEditModalRef.current?.handleOpen()
  }
  
  const handleDelete = (type: string) => {
    const newValue: SubmitTypes = {}
    Object.keys(value).forEach(key => {
      if (key !== type) {
        newValue[key] = value[key]
      }
    })

    onChange?.(newValue)
  }
  
  const handleSave = (type: string) => {
    onChange?.({
      ...value,
      [type]: { type, enabled: true }
    })
  }

  const getTypeConfig = (type: string) => {
    return submitTypes.find(item => item.type === type)
  }
  const handleChangeStatus = (type: string) => {
    onChange?.({
      ...value,
      [type]: {
        ...value[type],
        enabled: !value[type]?.enabled
      }
    })
  }

  return (
    <>
      <Flex gap={10} vertical className="rb:mb-3!">
        <Flex align="center" justify="space-between" className="rb:mb-2!">
          <Flex align="center" gap={4} className="rb:text-[12px] rb:font-medium rb:leading-4.5">
            {t('workflow.config.human-intervention.delivery_method')}
            <Tooltip title={t('workflow.config.human-intervention.submit_type_tip')}>
              <div className="rb:size-3.5 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')]"></div>
            </Tooltip>
          </Flex>

          <Button
            onClick={handleAdd}
            className="rb:py-0! rb:px-1! rb:h-4.5! rb:rounded-sm! rb:text-[12px]!"
            size="small"
          >
            +
          </Button>
        </Flex>
        {/* <Button type="dashed" block size="middle" className="rb:text-[12px]!" onClick={handleAdd}>+ {t('workflow.config.human-intervention.addSubmitType')}</Button> */}

        {Object.keys(value)?.length > 0
          ? Object.keys(value)?.map((type, index) => {
            const typeConfig = getTypeConfig(type)
            return (
              <div
                key={index}
                className="rb:cursor-pointer rb:group rb:py-2 rb:pl-2.5 rb:pr-2 rb:text-[12px] rb-border rb:rounded-md rb:relative"
              >
                <Flex align="center" className="rb:leading-4 rb:w-full! rb:overflow-hidden rb:whitespace-nowrap rb:text-ellipsis" gap={2}>
                  <div className={typeConfig?.icon || ''}>{typeConfig?.iconText || ''}</div>
                  <span className="rb:font-medium rb:inline-block">{t(`workflow.config.human-intervention.submitTypes.${type}`)}</span>
                </Flex>

                  <Flex gap={10} align="center" justify="end" className="rb:absolute rb:w-22 rb:pr-3! rb:right-0 rb:top-0 rb:bottom-0 rb:bg-[linear-gradient(90deg,rgba(255,255,255,0.5)_0%,#FFFFFF_50%)] rb:shadow-[0px_2px_4px_0px rgba(0,0,0,0.06)] rb:rounded-[0px_8px_8px_0px]">
                    {/* {typeConfig?.hasConfig && (
                      <div
                        className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/edit.svg')] rb:hover:bg-[url('@/assets/images/edit_hover.svg')]"
                        onClick={() => handleEdit(type)}
                      ></div>
                    )} */}
                    <div
                      className="rb:size-4 rb:cursor-pointer rb:bg-cover  rb:bg-[url('@/assets/images/delete.svg')] rb:hover:bg-[url('@/assets/images/delete_hover.svg')]"
                      onClick={() => handleDelete(type)}
                    ></div>
                    <Switch checked={value[type]?.enabled} onChange={() => handleChangeStatus(type)} />
                  </Flex>
              </div>
            )
          })
          : <Flex align="center" justify="center"
            className="rb:bg-[#F6F6F6] rb:rounded-lg rb:h-12.5 rb:mb-3! rb:text-[12px]"
          >
            {t('workflow.config.human-intervention.noDeliveryMethod')}
          </Flex>
        }
      </Flex>

      <SubmitTypeEditModal
        ref={submitTypeEditModalRef}
        refresh={handleSave}
      />
      
      {/* <EmailConfigModal
        ref={emailConfigModalRef}
        onSave={handleEmailConfigSave}
      /> */}
    </>
  )
}

export default SubmitTypeList
