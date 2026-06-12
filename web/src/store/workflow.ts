/*
 * @Author: ZhaoYing 
 * @Date: 2026-04-10 18:11:19 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-04-10 18:11:19 
 */
import { create } from 'zustand'
import type { NodeCheckResult } from '@/views/Workflow/components/CheckList'
import type { ChatItem } from '@/components/Chat/types'

interface WorkflowState {
  checkResults: Record<string, NodeCheckResult[]>
  setCheckResults: (results: NodeCheckResult[], appId?: string) => void
  getCheckResults: (appId?: string) => NodeCheckResult[]
  chatHistoryMap: Record<string, ChatItem[]>
  setChatHistory: (conversationId: string, history: ChatItem[]) => void
  getChatHistory: (conversationId: string) => ChatItem[]
}

export const useWorkflowStore = create<WorkflowState>((set, get) => ({
  checkResults: {},
  setCheckResults: (results, appId) =>{
    if (!appId) return
    return set(state => ({ checkResults: { ...state.checkResults, [appId]: results } }))
  },
  getCheckResults: (appId) => {
    if (!appId) return []
    return get().checkResults[appId] ?? []
  },
  chatHistoryMap: {},
  setChatHistory: (conversationId, history) =>
    set(state => ({ chatHistoryMap: { ...state.chatHistoryMap, [conversationId]: history } })),
  getChatHistory: (conversationId) => get().chatHistoryMap[conversationId] ?? [],
}))
