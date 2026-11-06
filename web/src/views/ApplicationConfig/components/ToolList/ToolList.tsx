import { type FC, useRef, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Flex } from 'antd'

import Card from '../Card'
import type { ToolModalRef, ToolEntry, ToolItem, ToolMethod } from './types'
import Empty from '@/components/Empty'
import ToolModal from './ToolModal'
import ToolRow from './ToolRow'
import { getToolMethodsBatch } from '@/api/tools'
import Tag from '@/components/Tag'
import { groupToolEntries } from './selection'

interface ToolListProps {
  value?: ToolEntry[];
  onChange?: (entries: ToolEntry[]) => void;
}

interface DisplayTool {
  tool: ToolItem;
  methods: ToolMethod[];
  entries: ToolEntry[];
}

const ToolList: FC<ToolListProps> = ({ value, onChange }) => {
  const { t } = useTranslation()
  const toolModalRef = useRef<ToolModalRef>(null)
  const [displayTools, setDisplayTools] = useState<DisplayTool[]>([])

  useEffect(() => {
    if (!value || value.length === 0) {
      setDisplayTools([])
      return
    }

    const toolIds = [...new Set(value.map(e => e.tool_id))]

    getToolMethodsBatch(toolIds).then(res => {
      const batchData = res as { items: Array<{ tool_id: string; name: string; tool_type: string; is_active: boolean; methods: ToolMethod[] }>; missing_tool_ids: string[] }
      const toolMap = new Map(batchData.items.map(item => [item.tool_id, item]))

      const groupedByTool = new Map<string, ToolEntry[]>()
      for (const entry of value) {
        const existing = groupedByTool.get(entry.tool_id) || []
        existing.push(entry)
        groupedByTool.set(entry.tool_id, existing)
      }

      const displayList: DisplayTool[] = []
      for (const [toolId, entries] of groupedByTool) {
        const batchItem = toolMap.get(toolId)
        if (!batchItem) continue

        const tool: ToolItem = {
          id: batchItem.tool_id,
          name: batchItem.name,
          description: '',
          icon: null,
          tool_type: batchItem.tool_type as ToolItem['tool_type'],
          method_count: batchItem.methods.length,
          is_active: batchItem.is_active,
        }

        displayList.push({
          tool,
          methods: batchItem.methods,
          entries,
        })
      }

      setDisplayTools(displayList)
    }).catch(() => {
      setDisplayTools([])
    })
  }, [value])

  const handleAddTool = () => {
    toolModalRef.current?.handleOpen()
  }

  const handleRefresh = (newEntries: ToolEntry[]) => {
    const currentEntries = value || []
    const merged = groupToolEntries([...currentEntries, ...newEntries])
    onChange?.(merged)
  }

  const handleDeleteTool = (toolId: string, operation?: string | null) => {
    const currentEntries = value || []
    const filtered = currentEntries.filter(e => {
      if (e.tool_id !== toolId) return true
      if (operation === undefined) return false
      return e.operation !== operation
    })
    onChange?.(filtered)
  }

  return (
    <Card
      title={t('application.toolConfiguration')}
      extra={
        <Button className="rb:h-6! rb:py-0! rb:px-2! rb:rounded-md! rb:text-gray-800" onClick={handleAddTool}>
          + {t('application.addTool')}
        </Button>
      }
    >
      <div className="rb:leading-4.5 rb:text-[12px] rb:mb-2 rb:font-medium">
        {t('application.toolManagement')}
      </div>
      {displayTools.length === 0
        ? <div className="rb-border rb:rounded-xl rb:pt-4 rb:pb-6"><Empty size={88} /></div>
        : <Flex vertical gap={12}>
            {displayTools.map((displayTool) => {
              return (
                <div key={displayTool.tool.id} className="rb:px-3 rb:py-1 rb:bg-white rb-border rb:rounded-lg">
                  <ToolRow
                    tool={displayTool.tool}
                    methods={displayTool.methods}
                    trailing={
                      <>
                        {displayTool.tool.is_active === false && (
                          <Tag color="error" className="rb:shrink-0">{t('common.deleted')}</Tag>
                        )}
                        <div
                          className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/delete.svg')] rb:hover:bg-[url('@/assets/images/common/delete_red.svg')]"
                          onClick={() => handleDeleteTool(displayTool.tool.id)}
                        />
                      </>
                    }
                    renderMethod={(method, methodIndex) => {
                      const operationKey = method.operation_key
                      const entry = displayTool.entries.find(e => e.operation === operationKey)
                      if (!entry) return null

                      return (
                        <div key={method.method_id || methodIndex} className="rb:flex rb:items-center rb:gap-2 rb:min-h-9 rb:pl-2">
                          <span className="rb:min-w-0 rb:flex-1 rb:truncate rb:text-sm" title={method.name}>
                            {method.name}
                          </span>
                          {displayTool.tool.is_active === false && (
                            <Tag color="error" className="rb:shrink-0">{t('common.deleted')}</Tag>
                          )}
                          <div
                            className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/delete.svg')] rb:hover:bg-[url('@/assets/images/common/delete_red.svg')]"
                            onClick={() => handleDeleteTool(displayTool.tool.id, operationKey)}
                          />
                        </div>
                      )
                    }}
                  />
                </div>
              )
            })}
          </Flex>
      }
      <ToolModal
        ref={toolModalRef}
        existingEntries={value || []}
        refresh={handleRefresh}
      />
    </Card>
  )
}

export default ToolList
