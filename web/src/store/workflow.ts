/*
 * @Author: ZhaoYing 
 * @Date: 2026-04-10 18:11:19 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-04-10 18:11:19 
 */
import { create } from 'zustand'
import type { NodeCheckResult } from '@/views/Workflow/components/CheckList'

interface WorkflowState {
  checkResults: Record<string, NodeCheckResult[]>
  setCheckResults: (appId: string, results: NodeCheckResult[]) => void
  getCheckResults: (appId: string) => NodeCheckResult[]
}

export const useWorkflowStore = create<WorkflowState>((set, get) => ({
  checkResults: {},
  setCheckResults: (appId, results) =>
    set(state => ({ checkResults: { ...state.checkResults, [appId]: results } })),
  getCheckResults: (appId) => get().checkResults[appId] ?? [],
}))
