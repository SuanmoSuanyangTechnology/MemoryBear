import { createContext, useContext } from 'react'
import type { Graph, Node } from '@antv/x6'
import type { FormInstance } from 'antd'
import type { NodeConfig } from '../../types'
import type { Suggestion } from '../Editor/plugin/AutocompletePlugin'
import type { Model } from '@/views/ModelManagement/types'
import type { Application } from '@/views/ApplicationManagement/types'
import type { Memory } from '@/views/MemoryManagement/types'

/**
 * Shared context value for the Properties panel and its sub components.
 * Centralizes the node/form state so extracted field components can consume
 * it via `useProperties` instead of prop drilling.
 */
export interface PropertiesContextValue {
  /** Currently selected node */
  selectedNode: Node
  /** Reference to graph instance */
  graphRef: React.MutableRefObject<Graph | undefined>
  /** Ant Design form instance */
  form: FormInstance<NodeConfig>
  /** Watched form values */
  values: NodeConfig
  /** Raw data of the selected node */
  data: any
  /** Variable suggestion list */
  variableList: Suggestion[]
  /** Config map of the selected node */
  configs: Record<string, NodeConfig>
  /** Application type */
  appType?: Application['type']
  /** Active memory config */
  activeMemoryConfig?: Memory | null
  /** Model options loaded by ModelSelect */
  modelOptions: Model[]
  /** Setter for model options */
  setModelOptions: React.Dispatch<React.SetStateAction<Model[]>>
  /** Whether advanced settings are expanded */
  advancedSettingsCollapsed: boolean
  /** Setter for advanced settings expand state */
  setAdvancedSettingsCollapsed: React.Dispatch<React.SetStateAction<boolean>>
  /** Get filtered variable list based on node type and config key */
  getFilteredVariableList: (nodeType?: string, key?: string) => Suggestion[]
  /** Handler for blank canvas click */
  blankClick: () => void
  /** Handler for node click */
  nodeClick: ({ node }: { node: Node }) => void
}

const PropertiesContext = createContext<PropertiesContextValue | null>(null)

export const PropertiesProvider = PropertiesContext.Provider

/**
 * Access the Properties panel shared context.
 * @throws when used outside of a PropertiesProvider
 */
export const useProperties = (): PropertiesContextValue => {
  const ctx = useContext(PropertiesContext)
  if (!ctx) {
    throw new Error('useProperties must be used within a PropertiesProvider')
  }
  return ctx
}
