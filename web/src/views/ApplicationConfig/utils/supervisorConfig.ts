import type { KnowledgeConfig } from '@/components/Knowledge/types'
import type { Skill } from '@/views/Skills/types'
import type { SupervisorConfig } from '../types'

const normalizeKnowledgeForSave = (
  knowledgeConfig: KnowledgeConfig | null | undefined,
  originalKnowledgeConfig: KnowledgeConfig | null | undefined
): KnowledgeConfig | null => {
  const { knowledge_bases: knowledgeBases = [], ...knowledgeConfigRest } = knowledgeConfig ?? {}
  if (knowledgeBases.length === 0) return null

  return {
    ...originalKnowledgeConfig,
    ...knowledgeConfigRest,
    knowledge_bases: knowledgeBases.map(item => {
      const itemConfig = item.config ?? item
      return {
        kb_id: item.kb_id || item.id,
        ...itemConfig,
      }
    }),
  } as KnowledgeConfig
}

const isDefaultSupervisorConfig = (supervisorConfig: SupervisorConfig): boolean => {
  const knowledgeBases = supervisorConfig.knowledge_retrieval?.knowledge_bases ?? []
  const skills = supervisorConfig.skills

  return !supervisorConfig.system_prompt
    && supervisorConfig.memory?.enabled !== true
    && knowledgeBases.length === 0
    && skills?.enabled !== true
    && skills?.all_skills !== true
    && (skills?.skill_ids?.length ?? 0) === 0
    && (supervisorConfig.variables?.length ?? 0) === 0
    && (supervisorConfig.tools?.length ?? 0) === 0
}

/** Build stable form values when the API returns a null or partial supervisor config. */
export const normalizeSupervisorConfigForForm = (
  supervisorConfig: SupervisorConfig | null | undefined
): SupervisorConfig => {
  const skillIds = (supervisorConfig?.skills?.skill_ids ?? []).map(skill => (
    typeof skill === 'string' ? { id: skill } : skill
  )) as Skill[]

  return {
    ...supervisorConfig,
    system_prompt: supervisorConfig?.system_prompt ?? null,
    memory: {
      ...supervisorConfig?.memory,
      enabled: supervisorConfig?.memory?.enabled ?? false,
    },
    skills: {
      ...supervisorConfig?.skills,
      enabled: supervisorConfig?.skills?.enabled ?? (supervisorConfig?.skills != null),
      all_skills: supervisorConfig?.skills?.all_skills ?? false,
      skill_ids: skillIds,
    },
    variables: (supervisorConfig?.variables ?? []).map((variable, index) => ({
      ...variable,
      index,
    })),
  }
}

/** Convert supervisor form values back to the multi-agent API contract. */
export const normalizeSupervisorConfigForSave = (
  supervisorConfig: SupervisorConfig | null | undefined,
  originalSupervisorConfig?: SupervisorConfig | null
): SupervisorConfig | null | undefined => {
  if (!supervisorConfig) return supervisorConfig
  if (originalSupervisorConfig === null && isDefaultSupervisorConfig(supervisorConfig)) {
    return null
  }

  return {
    ...originalSupervisorConfig,
    ...supervisorConfig,
    knowledge_retrieval: normalizeKnowledgeForSave(
      supervisorConfig.knowledge_retrieval,
      originalSupervisorConfig?.knowledge_retrieval
    ),
    variables: supervisorConfig.variables?.map(variable => {
      const normalizedVariable = { ...variable }
      delete normalizedVariable.index
      delete normalizedVariable.key
      return normalizedVariable
    }),
  }
}
