/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:26:03 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-19 21:22:53
 */
/**
 * Tool List Component
 * Manages tool configurations for the application
 * Allows adding, removing, and enabling/disabling tools
 */

import { type FC, useRef, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Space, Button, Switch, Flex } from 'antd'

import Card from '../Card'
import type {
  ToolModalRef,
  ToolOption
} from './types'
import Empty from '@/components/Empty'
import ToolModal from './ToolModal'
import { getToolMethods, getToolDetail } from '@/api/tools'
import Tag from '@/components/Tag'

/**
 * Tool list management component
 * @param value - Current tool configurations
 * @param onChange - Callback when tools change
 */
const ToolList: FC<{ value?: ToolOption[]; onChange?: (config: ToolOption[]) => void}> = ({value, onChange}) => {
  const { t } = useTranslation()
  const toolModalRef = useRef<ToolModalRef>(null)
  const [toolList, setToolList] = useState<ToolOption[]>([])
  useEffect(() => {
    if (value) {
      const processedData = value.map(async (item) => {
        if (!item.label && item.tool_id) {
          try {
            const [toolDetail, methods] = await Promise.all([
              getToolDetail(item.tool_id),
              getToolMethods(item.tool_id)
            ])

            switch ((toolDetail as any).tool_type) {
              case 'mcp':
                const mcpFilterItem = (methods as any[]).find(vo => vo.name === item.operation)
                return {
                  ...item,
                  is_active: (toolDetail as any).is_active,
                  label: mcpFilterItem?.description,
                  method_id: mcpFilterItem?.method_id,
                  value: mcpFilterItem?.name,
                  description: mcpFilterItem?.description,
                  parameters: mcpFilterItem?.parameters
                }
              case 'builtin':
                if ((methods as any[]).length > 1) {
                  const builtinFilterItem = (methods as any[]).find(vo => vo.name === item.operation)
                  return {
                    ...item,
                    is_active: (toolDetail as any).is_active,
                    label: builtinFilterItem?.description,
                    method_id: builtinFilterItem?.method_id,
                    value: builtinFilterItem?.name,
                    description: builtinFilterItem?.description,
                    parameters: builtinFilterItem?.parameters
                  }
                }
                return {
                  ...item,
                  is_active: (toolDetail as any).is_active,
                  label: (methods as any[])[0]?.description,
                  method_id: (methods as any[])[0]?.method_id,
                  value: (methods as any[])[0]?.name,
                  description: (methods as any[])[0]?.description,
                  parameters: (methods as any[])[0]?.parameters
                }
              default:
                const customFilterItem = (methods as any[]).find(vo => vo.method_id === item.operation)
                return {
                  ...item,
                  is_active: (toolDetail as any).is_active,
                  label: customFilterItem?.name,
                  method_id: customFilterItem?.method_id,
                  value: customFilterItem?.name,
                  description: customFilterItem?.description,
                  parameters: customFilterItem?.parameters
                }
            }
          } catch (error) {
            return item
          }
        }
        return item
      })
      
      Promise.all(processedData).then(setToolList)
    }
  }, [value])

  /** Open tool selection modal */
  const handleAddTool = () => {
    toolModalRef.current?.handleOpen()
  }
  /** Add new tool to list */
  const updateTools = (tool: ToolOption) => {
    const list = [...toolList, {
      ...tool,
      is_active: true,
    }]
    setToolList(list)
    onChange && onChange(list)
  }
  /** Remove tool from list */
  const handleDeleteTool = (index: number) => {
    const list = toolList.filter((_item, idx) => idx !== index)
    setToolList([...list])
    onChange && onChange(list)
  }
  /** Toggle tool enabled state */
  const handleChangeEnabled = (index: number) => {
    const list = toolList.map((item, idx) => {
      if (idx === index) {
        return {
          ...item,
          enabled: !item.enabled
        }
      }
      return item
    })
    setToolList([...list])
    onChange && onChange(list)
  }
  return (
    <Card 
      title={t('application.toolConfiguration')}
      extra={
        <Button className="rb:h-6! rb:py-0! rb:px-2! rb:rounded-md! rb:text-[#21233" onClick={handleAddTool}>+ {t('application.addTool')}</Button>
      }
    >
      <div className="rb:leading-4.5 rb:text-[12px] rb:mb-2 rb:font-medium">
        {t('application.toolManagement')}
      </div>
      {toolList.length === 0
        ? <div className="rb-border rb:rounded-xl rb:pt-4 rb:pb-6"><Empty size={88} /></div>
        : <Flex vertical gap={12}>
          {toolList.map((item, index) => (
            <Flex key={index} align="center" justify="space-between" className="rb:py-2.5! rb:pl-4! rb:pr-3! rb-border rb:rounded-lg">
              <div>
                <div className="rb:font-medium rb:leading-4">
                  {item.label}
                </div>
                <Tag color={item.is_active ? 'success' : 'error'} className="rb:mt-1">
                  {item.is_active ? t('common.enable') : t('common.deleted')}
                </Tag>
              </div>
              <Space size={12}>
                <Switch size="small" checked={item.enabled} onChange={() => handleChangeEnabled(index)} />
                <div
                  className="rb:w-6 rb:h-6 rb:cursor-pointer rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]"
                  onClick={() => handleDeleteTool(index)}
                ></div>
              </Space>
            </Flex>
          ))}
        </Flex>
      }
      <ToolModal
        ref={toolModalRef}
        refresh={updateTools}
      />
    </Card>
  )
}
export default ToolList