import type { ToolEntry, ToolMethod, ToolItem } from './types'

export const getOperationKey = (method: ToolMethod): string | null => method.operation_key

export const groupToolEntries = (entries: ToolEntry[]): ToolEntry[] => {
  const seen = new Set<string>()
  const result: ToolEntry[] = []
  for (const entry of entries) {
    const key = entry.operation ? `${entry.tool_id}:${entry.operation}` : entry.tool_id
    if (!seen.has(key)) {
      seen.add(key)
      result.push(entry)
    }
  }
  return result
}

export const isSingleEntryTool = (tool: ToolItem, methods: ToolMethod[]): boolean => {
  if (tool.tool_type === 'workflow') return true
  if (tool.tool_type === 'builtin') {
    const hasOperationEnum = methods.some(m => m.operation_key !== null)
    return !hasOperationEnum
  }
  return false
}
