/**
 * Tool List Component
 * Manages tool configurations for the application
 * Allows adding, removing, and enabling/disabling tools
 */

import { type FC, useRef, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Space, Button, Flex } from 'antd'

import Card from '@/views/ApplicationConfig/components/Card'
import type {
  ToolModalRef,
  ToolOption
} from './types'
import Empty from '@/components/Empty'
import ToolModal from './ToolModal'
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
  readOnly?: boolean;
}

/**
 * Tool list management component
 * @param value - Current tool configurations
 * @param onChange - Callback when tools change
 */
const ToolList: FC<ToolListProps> = ({value, onChange, readOnly = false}) => {
  const { t } = useTranslation()
  const toolModalRef = useRef<ToolModalRef>(null)
  const [toolList, setToolList] = useState<ToolOption[]>([])
  useEffect(() => {
    if (value) {
      const processedData = value.map(async (item) => {
        // Skip if tool already has label (already processed)
        if (!item.label && item.tool_id) {
          try {
            // Fetch tool details and methods in parallel
            const [toolDetail, methods] = await Promise.all([
              getToolDetail(item.tool_id),
              getToolMethods(item.tool_id)
            ])

            // Process based on tool type
            switch ((toolDetail as any).tool_type) {
              case 'mcp':
              case 'workflow':
                // MCP tools: Find method by operation name
                const mcpFilterItem = (methods as any[]).find(vo => vo.name === item.operation)
                return {
                  ...item,
                  is_active: (toolDetail as any).is_active,
                  label: (toolDetail as any).tool_type === 'workflow' ? mcpFilterItem?.name ||mcpFilterItem?.description : mcpFilterItem?.description,
                  method_id: mcpFilterItem?.method_id,
                  value: mcpFilterItem?.name,
                  description: mcpFilterItem?.description,
                  parameters: mcpFilterItem?.parameters
                }
                break
              case 'builtin':
                // Builtin tools: Handle single or multiple methods
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
                // Single method: Use first method
                return {
                  ...item,
                  is_active: (toolDetail as any).is_active,
                  label: (methods as any[])[0]?.description,
                  method_id: (methods as any[])[0]?.method_id,
                  value: (methods as any[])[0]?.name,
                  description: (methods as any[])[0]?.description,
                  parameters: (methods as any[])[0]?.parameters
                }
                break
              default:
                // Custom tools: Find method by method_id
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
            // Return original item if fetch fails
            return item
          }
        }
        return item
      })
      
      // Wait for all tools to be processed
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
  
  const content = (
    <>
      {toolList.length === 0
        ? <div className="rb-border rb:rounded-xl rb:pt-4 rb:pb-6"><Empty size={88} /></div>
        : <Flex vertical gap={12} className={readOnly ? 'rb:grid! rb:grid-cols-2 rb:[@media(max-width:600px)]:grid-cols-1 rb:[&>.ant-flex]:min-w-0 rb:[&>.ant-flex>div:first-child]:min-w-0 rb:[&>.ant-flex>div:first-child]:[overflow-wrap:anywhere]' : undefined}>
          {toolList.map((item, index) => (
            <Flex key={index} align="center" justify="space-between" className="rb:py-2.5! rb:pl-4! rb:pr-3! rb-border rb:rounded-lg">
              <div>
                <div className="rb:font-medium rb:leading-4">
                  {item.label || item.operation || item.tool_id}
                </div>
                {(!readOnly || item.is_active !== undefined) &&
                  <Tag color={item.is_active ? 'success' : 'error'} className="rb:mt-1">
                    {item.is_active ? t('common.enable') : t('common.deleted')}
                  </Tag>
                }
              </div>
              {!readOnly &&
                <Space size={12}>
                  <div
                    className="rb:w-6 rb:h-6 rb:cursor-pointer rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]"
                    onClick={() => handleDeleteTool(index)}
                  ></div>
                </Space>
              }
            </Flex>
          ))}
        </Flex>
      }
      {!readOnly &&
        <ToolModal
          ref={toolModalRef}
          refresh={updateTools}
        />
      }
    </>
  )

  if (readOnly) {
    return (
      <section aria-label={t('application.toolConfiguration')}>
        <div className="rb:mb-2">{t('application.toolConfiguration')}</div>
        {content}
      </section>
    )
  }

  return (
    <Card
      title={t('application.toolConfiguration')}
      extra={
        !readOnly && <Button className="rb:h-6! rb:py-0! rb:px-2! rb:rounded-md! rb:text-gray-800" onClick={handleAddTool}>+ {t('application.addTool')}</Button>
      }
    >
      {content}
    </Card>
  )
}
export default ToolList
