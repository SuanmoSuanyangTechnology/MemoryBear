import { home } from './en/home'
import { account } from './en/account'
import { model } from './en/model'
import { knowledgeBase } from './en/knowledgeBase'
import { application } from './en/application'
import { userMemory } from './en/userMemory'
import { space } from './en/space'
import { tool } from './en/tool'
import { workflow } from './en/workflow'
import { engine } from './en/engine'
import { detail } from './en/detail'
import { notificationCenter } from './en/notificationCenter'
import { memoryConversation } from './en/memoryConversation'
import { skills } from './en/skills';

export const en = {
  translation: {
    resourceSharing: {
      catalog: 'Share to resource center',
      editable: 'Allow editable copies',
      market: 'Submit to community market',
      description: 'Resource description',
      descriptionRequired: 'Enter a resource description',
      pendingSync: 'Sharing settings saved. Community sync is pending; submit again to retry.',
      failed: 'Sharing failed. Please retry.',
    },
    resourceCenter: {
      taskInput: 'Task input', resultSummary: 'Result summary',
      multi_agent: 'Multi-agent', examples: 'Usage examples', flowView: 'Workflow', collaboration: 'Agents and collaboration',
      nodeCount: '{{count}} nodes', agentCount: '{{count}} agents',
      editPermission: 'Edit permission', enabled: 'Enabled', disabled: 'Disabled',
      relatedSkills: 'Related skills', relatedTools: 'Related tools', updatedAt: 'Last updated', updateNotes: 'Release notes',
      supervisor: 'Supervisor', parallel: 'Parallel execution', knowledgeAgent: 'Knowledge agent', salesAgent: 'Sales agent', financeAgent: 'Finance agent',
      start: 'Start', parse: 'Parse document', check: 'Check compliance', risk: 'Risks and suggestions', approval: 'Approval summary', output: 'Review report',
      supervisorHint: 'Parallel execution', knowledgeAgentHint: 'Review project requirements', salesAgentHint: 'Evaluate customer needs', financeAgentHint: 'Analyze financial risks',
      startHint: 'Input document', parseHint: 'Extract text and key fields', checkHint: 'Check company requirements', riskHint: 'Identify issues and changes', approvalHint: 'Summarize review findings', outputHint: 'Compile review results',
      zoomIn: 'Zoom in', zoomOut: 'Zoom out', fit: 'Fit view',
      title: 'Applications', subtitle: 'Applications shared within your tenant', banner: 'Banner placeholder',
      search: 'Search name, description or publisher', all: 'All', knowledge: 'Knowledge',
      office: 'Productivity', sales: 'Sales', writing: 'Content creation',
      add: 'Add to workspace', detail: 'Application details', publisher: 'Publisher', version: 'Version',
      type: 'Application type', views: 'Views', skills: 'Skills', tools: 'Tools',
      agent: 'Agent', workflow: 'Workflow', space: 'Target workspace', selectSpace: 'Select a workspace',
      mockNotice: 'Demo data only. Additions last for this page session and do not create real applications.',
      noUpdateNotes: 'No update notes', added: 'Added to workspace', duplicate: 'Already added to this workspace. Choose another workspace.',
      failed: 'Operation failed. Please retry.', empty: 'No matching shared applications', more: 'Scroll down to load more',
    },
    ...home,
    ...account,
    ...model,
    ...knowledgeBase,
    ...application,
    ...userMemory,
    ...space,
    tool,
    skills,
    ...workflow,
    ...engine,
    ...detail,
    ...notificationCenter,
    ...memoryConversation,
  },
}
