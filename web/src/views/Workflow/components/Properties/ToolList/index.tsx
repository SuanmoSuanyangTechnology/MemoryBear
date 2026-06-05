/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:26:03 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-04 19:53:58
 */
/**
 * Tool List Component
 * Manages tool configurations for the application
 * Allows adding, removing, and enabling/disabling tools
 */

import { type FC, useRef, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Space, Button, Flex, Switch } from 'antd'

import type {
  ToolModalRef,
  ToolOption
} from './types'
import Empty from '@/components/Empty'
import ToolModal from '@/views/ApplicationConfig/components/ToolList/ToolModal'
import { getToolMethods, getToolDetail } from '@/api/tools'
import Tag from '@/components/Tag'

/**
 * Tool List Component Props
 */
interface ToolListProps {
  /** Current tool configurations */
  value?: ToolOption[];
  /** Callback when tools change */
  onChange?: (config: ToolOption[]) => void;
}

/**
 * Tool list management component
 * @param value - Current tool configurations
 * @param onChange - Callback when tools change
 */
const ToolList: FC<ToolListProps> = ({value, onChange}) => {
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

  /**
   * Opens the tool selection modal
   */
  const handleAddTool = () => {
    toolModalRef.current?.handleOpen()
  }
  
  /**
   * Adds a new tool to the list
   * Updates both local state and parent component
   * @param tool - Tool to add
   */
  const updateTools = (tool: ToolOption) => {
    const list = [...toolList, {
      ...tool,
      is_active: true,
    }]
    setToolList(list)
    onChange && onChange(list)
  }
  
  /**
   * Removes a tool from the list by index
   * Updates both local state and parent component
   * @param index - Index of tool to remove
   */
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
    <>
      <Flex align="center" justify="space-between" className="rb:mb-2!">
        <div className="rb:font-medium rb:text-[12px] rb:leading-4.5">{t('application.toolConfiguration')}</div>
        <Button
          onClick={handleAddTool} 
          size="small"
          className="rb:text-[12px]! rb:rounded-sm!"
        >
          + {t('common.add')}
        </Button>
      </Flex>
      {toolList.length === 0
        ? <div className="rb-border rb:rounded-xl rb:pt-4 rb:pb-6"><Empty size={88} /></div>
        : <Flex vertical gap={12} className="rb:text-[12px]">
          {toolList.length === 0
        ? <div className="rb-border rb:rounded-xl rb:pt-4 rb:pb-6"><Empty size={88} /></div>
        : <Flex vertical gap={12}>
            {toolList.map((item, index) => (
              <Flex gap={12} key={index} align="center" justify="space-between" className="rb:py-2.5! rb:pl-4! rb:pr-3! rb-border rb:rounded-lg">
                <div>
                  <div className="rb:leading-4">
                    {item.label}
                  </div>
                  <Tag color={item.is_active ? 'success' : 'error'} className="rb:mt-1">
                    {item.is_active ? t('common.enable') : t('common.deleted')}
                  </Tag>
                </div>
                <Space size={12}>
                  <Switch size="small" checked={item.enabled} onChange={() => handleChangeEnabled(index)} />
                  <div
                    className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/delete.svg')] rb:hover:bg-[url('@/assets/images/common/delete_hover.svg')]"
                    onClick={() => handleDeleteTool(index)}
                  ></div>
                </Space>
              </Flex>
            ))}
          </Flex>
        }
        </Flex>
      }
      <ToolModal
        ref={toolModalRef}
        refresh={updateTools}
      />
    </>
  )
}
export default ToolList
