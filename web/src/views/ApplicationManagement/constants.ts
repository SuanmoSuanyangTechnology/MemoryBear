import AgentIcon from '@/assets/images/application/agent.svg'
import ClusterIcon from '@/assets/images/application/multi_agent.svg'
import WorkflowIcon from '@/assets/images/application/workflow.svg'
import PureWorkflowIcon from '@/assets/images/application/pure_workflow.svg'
/**
 * Supported application types
 */
export const types = [
  'agent',
  'multi_agent',
  'workflow',
  'pure_workflow'
]

/**
 * Application type icon mapping
 */
export const typeIcons: Record<string, string> = {
  agent: AgentIcon,
  multi_agent: ClusterIcon,
  workflow: WorkflowIcon,
  pure_workflow: PureWorkflowIcon,
}