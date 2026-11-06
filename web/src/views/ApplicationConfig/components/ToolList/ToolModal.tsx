import { forwardRef, useImperativeHandle, useState, useEffect } from 'react'
import { Flex, Tree, Spin } from 'antd'
import type { DataNode } from 'antd/es/tree'
import { DownOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'

import type { ToolModalRef, ToolEntry, ToolItem, ToolMethod } from './types'
import { getOperationKey, isSingleEntryTool } from './selection'
import RbModal from '@/components/RbModal'
import { getTools, getToolMethods } from '@/api/tools'
import type { ToolType } from '@/views/ToolManagement/types'
import SearchInput from '@/components/SearchInput'
import PageTabs from '@/components/PageTabs'
import Empty from '@/components/Empty'
import ToolIcon from '@/views/ToolManagement/components/ToolIcon'

type ToolNode = DataNode & { tool?: ToolItem; method?: ToolMethod; parent?: ToolItem }

interface ToolModalProps {
  refresh: (entries: ToolEntry[]) => void;
  existingEntries: ToolEntry[];
}

type FilterType = ToolType

const EXPANDABLE_TYPES: ToolType[] = ['mcp', 'custom']

const tabOptions = [
  { value: 'builtin', label: 'tool.inner' },
  { value: 'mcp', label: 'tool.mcp' },
  { value: 'workflow', label: 'tool.workflow' },
  { value: 'custom', label: 'tool.custom' },
]

const ToolModal = forwardRef<ToolModalRef, ToolModalProps>(({ refresh, existingEntries }, ref) => {
  const { t } = useTranslation()
  const [visible, setVisible] = useState(false)
  const [filterType, setFilterType] = useState<FilterType>('builtin')
  const [search, setSearch] = useState<string | undefined>(undefined)
  const [selectedEntries, setSelectedEntries] = useState<ToolEntry[]>([])
  const [tools, setTools] = useState<ToolItem[]>([])
  const [loading, setLoading] = useState(false)
  const [expandedKeys, setExpandedKeys] = useState<string[]>([])
  const [expandedMethods, setExpandedMethods] = useState<Record<string, ToolMethod[]>>({})

  const handleClose = () => {
    setVisible(false)
    setSelectedEntries([])
    setExpandedKeys([])
    setExpandedMethods({})
  }

  const handleOpen = () => {
    setFilterType('builtin')
    setSearch(undefined)
    setSelectedEntries([])
    setExpandedKeys([])
    setExpandedMethods({})
    setVisible(true)
  }

  useImperativeHandle(ref, () => ({ handleOpen, handleClose }))

  useEffect(() => {
    if (!visible) return
    setSelectedEntries([...existingEntries])
  }, [visible, existingEntries])

  useEffect(() => {
    if (!visible) return
    setExpandedKeys([])
    setExpandedMethods({})
    setLoading(true)
    getTools({
      tool_type: filterType,
      name: search?.trim() || undefined,
      status: 'available',
    }).then(res => {
      const toolsList = res as ToolItem[]
      setTools(toolsList)
      const expandableTools = toolsList.filter(t =>
        EXPANDABLE_TYPES.includes(t.tool_type) &&
        existingEntries.some(e => e.tool_id === t.id && e.operation)
      )
      if (expandableTools.length > 0) {
        Promise.all(expandableTools.map(t => loadMethods(t.id))).catch(() => {})
      }
    }).catch(() => {
      setTools([])
    }).finally(() => {
      setLoading(false)
    })
  }, [visible, filterType, search, existingEntries])

  const loadMethods = (toolId: string): Promise<ToolMethod[]> => {
    return getToolMethods(toolId).then(methods => {
      const response = methods as ToolMethod[]
      setExpandedMethods(prev => ({ ...prev, [toolId]: response }))
      return response
    })
  }

  const isExistingEntry = (e: ToolEntry): boolean => {
    return existingEntries.some(ex => ex.tool_id === e.tool_id && ex.operation === e.operation)
  }

  const toggleTool = (tool: ToolItem, methods: ToolMethod[]) => {
    const isSingle = isSingleEntryTool(tool, methods)
    const toolKey = tool.id
    const isWholeSelected = selectedEntries.some(e => e.tool_id === toolKey && !e.operation)

    if (isSingle) {
      if (isWholeSelected) {
        setSelectedEntries(prev => prev.filter(e => e.tool_id !== toolKey || isExistingEntry(e)))
      } else {
        setSelectedEntries(prev => [
          ...prev.filter(e => e.tool_id !== toolKey || isExistingEntry(e)),
          { tool_id: toolKey, operation: null, enabled: true }
        ])
      }
      return
    }

    const allMethodKeys = methods.map(m => getOperationKey(m)).filter(k => k !== null)
    const selectedMethodKeys = selectedEntries
      .filter(e => e.tool_id === toolKey && e.operation)
      .map(e => e.operation)
    const allSelected = allMethodKeys.every(k => selectedMethodKeys.includes(k))

    if (allSelected) {
      setSelectedEntries(prev => prev.filter(e => e.tool_id !== toolKey || isExistingEntry(e)))
    } else {
      const unselectedMethodKeys = allMethodKeys.filter(k => !selectedMethodKeys.includes(k))
      const newEntries = unselectedMethodKeys.map(opKey => ({
        tool_id: toolKey,
        operation: opKey,
        enabled: true
      }))
      setSelectedEntries(prev => [
        ...prev.filter(e => e.tool_id !== toolKey || isExistingEntry(e)),
        ...newEntries
      ])
    }
  }

  const toggleMethod = (tool: ToolItem, method: ToolMethod) => {
    const opKey = getOperationKey(method)
    if (opKey === null) {
      toggleTool(tool, [method])
      return
    }

    const exists = selectedEntries.some(e =>
      e.tool_id === tool.id && e.operation === opKey
    )

    if (exists) {
      setSelectedEntries(prev => prev.filter(e =>
        !(e.tool_id === tool.id && e.operation === opKey) || isExistingEntry(e)
      ))
    } else {
      setSelectedEntries(prev => [
        ...prev.filter(e => !(e.tool_id === tool.id && !e.operation)),
        { tool_id: tool.id, operation: opKey, enabled: true }
      ])
    }
  }

  const renderTree = (items: ToolItem[]) => {
    const checked: string[] = []
    const halfChecked: string[] = []
    const addedToolIds = new Set(existingEntries.filter(e => !e.operation).map(e => e.tool_id))
    const addedMethodKeys = new Map<string, Set<string>>()
    existingEntries.filter(e => e.operation).forEach(e => {
      if (!addedMethodKeys.has(e.tool_id)) addedMethodKeys.set(e.tool_id, new Set())
      addedMethodKeys.get(e.tool_id)!.add(e.operation!)
    })

    const makeNode = (tool: ToolItem, methods: ToolMethod[] | null): ToolNode => {
      const toolKey = `tool:${tool.id}`
      const isExpandable = EXPANDABLE_TYPES.includes(tool.tool_type)
      const isSingle = methods ? isSingleEntryTool(tool, methods) : false
      const toolAlreadyAdded = addedToolIds.has(tool.id)
      const addedMethods = addedMethodKeys.get(tool.id) ?? new Set<string>()
      const allMethodsAdded = methods && methods.length > 0 && methods.every(m => {
        const opKey = getOperationKey(m)
        return opKey !== null && addedMethods.has(opKey)
      })

      const toolWholeSelected = selectedEntries.some(e => e.tool_id === tool.id && !e.operation)
      const selectedMethodKeys = selectedEntries
        .filter(e => e.tool_id === tool.id && e.operation)
        .map(e => e.operation)
      const selectedMethodCount = selectedMethodKeys.length
      const allMethodsSelected = methods && methods.length > 0 && selectedMethodCount === methods.length && !isSingle

      if (toolWholeSelected || allMethodsSelected) {
        checked.push(toolKey)
      } else if (selectedMethodCount > 0) {
        halfChecked.push(toolKey)
      }

      const parentDisabled = !tool.is_active || toolAlreadyAdded || allMethodsAdded

      const children: ToolNode[] | undefined = isExpandable && methods && !isSingle && methods.length >= 1
        ? methods.map((method) => {
            const methodKey = `method:${tool.id}:${getOperationKey(method) ?? method.method_id}`
            const opKey = getOperationKey(method)
            const methodSelected = opKey !== null && selectedMethodKeys.includes(opKey)
            const methodAlreadyAdded = opKey !== null && addedMethods.has(opKey)
            if (methodSelected) checked.push(methodKey)

            return {
              key: methodKey,
              tool,
              method,
              parent: tool,
              disableCheckbox: !tool.is_active || toolAlreadyAdded || methodAlreadyAdded,
              isLeaf: true,
              title: (
                <div className={`rb:flex rb:min-w-0 rb:flex-1 rb:items-center rb:gap-2 rb:min-h-9 ${(!tool.is_active || toolAlreadyAdded || methodAlreadyAdded) ? 'rb:opacity-45 rb:cursor-not-allowed' : ''}`}>
                  <span className="rb:min-w-0 rb:flex-1 rb:truncate rb:text-sm" title={method.name}>
                    {method.name}
                  </span>
                </div>
              ),
            }
          })
        : undefined

      return {
        key: toolKey,
        tool,
        disableCheckbox: parentDisabled,
        isLeaf: !isExpandable || isSingle || !methods || methods.length === 0,
        title: (
          <div className={`rb:flex rb:min-w-0 rb:flex-1 rb:items-center rb:gap-2 rb:min-h-11 ${parentDisabled ? 'rb:opacity-45 rb:cursor-not-allowed' : ''}`}>
            <ToolIcon iconUrl={tool.icon} />
            <span className="rb:min-w-0 rb:flex-1 rb:truncate" title={tool.name}>{tool.name}</span>
          </div>
        ),
        children,
      }
    }

    const treeData: ToolNode[] = items.map(tool => {
      const isExpandable = EXPANDABLE_TYPES.includes(tool.tool_type)
      const methods = isExpandable ? expandedMethods[tool.id] : null
      const toolAlreadyAdded = addedToolIds.has(tool.id)

      if (methods) {
        return makeNode(tool, methods)
      }

      const toolKey = `tool:${tool.id}`
      const toolWholeSelected = selectedEntries.some(e => e.tool_id === tool.id && !e.operation)
      if (toolWholeSelected) checked.push(toolKey)

      return {
        key: toolKey,
        tool,
        disableCheckbox: !tool.is_active || toolAlreadyAdded,
        isLeaf: !isExpandable,
        title: (
          <div className={`rb:flex rb:min-w-0 rb:flex-1 rb:items-center rb:gap-2 rb:min-h-11 ${(!tool.is_active || toolAlreadyAdded) ? 'rb:opacity-45 rb:cursor-not-allowed' : ''}`}>
            <ToolIcon iconUrl={tool.icon} />
            <span className="rb:min-w-0 rb:flex-1 rb:truncate" title={tool.name}>{tool.name}</span>
          </div>
        ),
      }
    })

    return (
      <Tree<ToolNode>
        blockNode
        checkable
        checkStrictly
        selectedKeys={[]}
        expandedKeys={expandedKeys}
        checkedKeys={{ checked, halfChecked }}
        treeData={treeData}
        switcherIcon={<DownOutlined />}
        className="rb:[&_.ant-tree-treenode]:flex rb:[&_.ant-tree-treenode]:items-center rb:[&_.ant-tree-switcher]:order-last rb:[&_.ant-tree-switcher]:ml-2 rb:[&_.ant-tree-switcher]:flex rb:[&_.ant-tree-switcher]:items-center rb:[&_.ant-tree-switcher]:justify-center rb:[&_.ant-tree-switcher]:relative! rb:[&_.ant-tree-switcher::before]:absolute! rb:[&_.ant-tree-switcher::before]:top-[50%]! rb:[&_.ant-tree-switcher::before]:mt-0! rb:[&_.ant-tree-switcher::before]:transform! rb:[&_.ant-tree-switcher::before]:-translate-y-1/2!"
        onExpand={(keys, info) => {
          const node = info.node
          const toolKey = `tool:${node.tool?.id}`
          setExpandedKeys(keys.map(k => String(k)))
          if (node.tool && !expandedMethods[node.tool.id] && EXPANDABLE_TYPES.includes(node.tool.tool_type)) {
            loadMethods(node.tool.id).then(methods => {
              if (isSingleEntryTool(node.tool!, methods) || methods.length === 0) {
                setExpandedKeys(prev => prev.filter(k => k !== toolKey))
              }
            })
          }
        }}
        onCheck={(_, info) => {
          const node = info.node
          if (node.method) {
            toggleMethod(node.tool!, node.method)
          } else if (node.tool) {
            if (!EXPANDABLE_TYPES.includes(node.tool.tool_type)) {
              const toolAlreadyAdded = addedToolIds.has(node.tool.id)
              if (toolAlreadyAdded || !node.tool.is_active) return
              setSelectedEntries(prev => {
                const exists = prev.some(e => e.tool_id === node.tool!.id && !e.operation)
                if (exists) return prev.filter(e => e.tool_id !== node.tool!.id)
                return [...prev.filter(e => e.tool_id !== node.tool!.id), { tool_id: node.tool!.id, operation: null, enabled: true }]
              })
            } else {
              const methods = expandedMethods[node.tool.id]
              if (methods) {
                toggleTool(node.tool, methods)
              } else {
                loadMethods(node.tool.id).then(m => toggleTool(node.tool!, m))
              }
            }
          }
        }}
        onSelect={(_, info) => {
          const node = info.node
          if (node.method) {
            toggleMethod(node.tool!, node.method)
          } else if (node.tool) {
            if (!EXPANDABLE_TYPES.includes(node.tool.tool_type)) {
              const toolAlreadyAdded = addedToolIds.has(node.tool.id)
              if (toolAlreadyAdded || !node.tool.is_active) return
              setSelectedEntries(prev => {
                const exists = prev.some(e => e.tool_id === node.tool!.id && !e.operation)
                if (exists) return prev.filter(e => e.tool_id !== node.tool!.id)
                return [...prev.filter(e => e.tool_id !== node.tool!.id), { tool_id: node.tool!.id, operation: null, enabled: true }]
              })
            } else {
              const methods = expandedMethods[node.tool.id]
              if (methods) {
                toggleTool(node.tool, methods)
              } else {
                loadMethods(node.tool.id).then(m => toggleTool(node.tool!, m))
              }
            }
          }
        }}
      />
    )
  }

  return (
    <RbModal
      title={t('application.addTool')}
      open={visible}
      onCancel={handleClose}
      okText={t('application.addTool')}
      okButtonProps={{ disabled: selectedEntries.every(e => isExistingEntry(e)) }}
      onOk={() => { refresh(selectedEntries); handleClose() }}
      width={640}
    >
      <Flex vertical gap={16} className="rb:max-h-[calc(100vh-202px)] rb:overflow-hidden">
        <Flex align="center" justify="space-between" wrap gap={12}>
          <PageTabs
            value={filterType}
            options={tabOptions.map(opt => ({
              value: opt.value,
              label: t(opt.label)
            }))}
            onChange={value => {
              setFilterType(value as FilterType)
              setSearch(undefined)
            }}
          />
          <SearchInput
            key={visible ? 'open' : 'closed'}
            value={search}
            placeholder={t('application.searchTool')}
            onSearch={setSearch}
            variant="outlined"
            className="rb:w-50!"
          />
        </Flex>
        <div className="rb:overflow-y-auto rb:min-h-60 rb:max-h-[min(60vh,560px)]">
          {loading ? (
            <Flex justify="center" align="center" className="rb:min-h-60">
              <Spin />
            </Flex>
          ) : tools.length === 0 ? (
            <Empty subTitle={t('application.noTool')} />
          ) : (
            renderTree(tools)
          )}
        </div>
      </Flex>
    </RbModal>
  )
})

export default ToolModal
