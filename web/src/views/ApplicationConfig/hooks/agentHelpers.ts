/**
 * Pure, stateless helpers extracted from useAgent. Keeping them here (free of
 * React state / form access) makes the logic reusable and unit-testable, and
 * lets the hook focus on orchestration.
 */
import type { Config, MemoryConfig } from '../types'
import type { Variable } from '../components/VariableList/types'
import type { KnowledgeConfig, KnowledgeConfigForm } from '@/components/Knowledge/types'
import type { Skill } from '@/views/Skills/types'

/**
 * Build a variable list from a set of raw names (deduped, in first-seen order).
 * @param names - Variable names
 * @returns Variable list with default text-type descriptors
 */
export function buildVariablesFromNames(names: string[]): Variable[] {
  const unique = [...new Set(names)]
  return unique.map((name, index) => ({
    index,
    type: 'text',
    name,
    display_name: name,
    required: false,
  }))
}

/**
 * Extract `{{var}}` placeholders from a prompt and turn them into variables.
 * @param text - Prompt text
 * @returns Variable list derived from the placeholders
 */
export function extractPromptVariables(text: string): Variable[] {
  const names = text.match(/\{\{([^}]+)\}\}/g)?.map(match => match.slice(2, -2)) || []
  return buildVariablesFromNames(names)
}

/**
 * Find `{{var}}` names referenced in text that are not among the existing names.
 * @param text - Text to scan (may be empty / null)
 * @param existingNames - Names already defined
 * @returns Deduped list of referenced-but-undefined names
 */
export function findInvalidVariables(text: string | null | undefined, existingNames: string[]): string[] {
  const usedVars = [...new Set([...(text?.matchAll(/\{\{(\w+)\}\}/g) ?? [])].map(m => m[1]))]
  const validNames = new Set(existingNames)
  return usedVars.filter(v => !validNames.has(v))
}

/**
 * Build the payload for saving an agent configuration by merging the original
 * data with the current form values (knowledge / tools / skills normalisation).
 * @param data - Original loaded configuration
 * @param values - Current form values
 * @returns Config payload ready to send to the save API
 */
export function buildAgentSaveParams(data: Config, values: Config): Config {
  const { memory, knowledge_retrieval, tools, skills, ...rest } = values
  const { knowledge_bases = [], ...knowledgeRest } = knowledge_retrieval || {}
  // Get other necessary properties of memory from original data
  const originalMemory = data.memory || ({} as MemoryConfig)

  const params: Config = {
    ...data,
    ...rest,
    memory: {
      ...originalMemory,
      ...memory,
    },
    knowledge_retrieval: knowledge_bases.length > 0 ? {
      ...data.knowledge_retrieval,
      ...knowledgeRest,
      knowledge_bases: knowledge_bases.map((item: KnowledgeConfigForm) => {
        const kb_config = item.config || item;
        return {
          kb_id: item.kb_id || item.id,
          retrieve_type: kb_config.retrieve_type,
          top_k: kb_config.top_k,
          similarity_threshold: ['participle', 'semantic', 'graph'].includes(kb_config.retrieve_type || '') ? undefined : kb_config.similarity_threshold,
          vector_similarity_weight: kb_config.vector_similarity_weight,
          enable_graph_retrieval: kb_config.enable_graph_retrieval,
          // ...(item.config || {})
        }
      })
    } as KnowledgeConfig : null,
    tools: tools.map(vo => {
      if (!vo.operation) {
        return {
          tool_id: vo.tool_id,
          enabled: vo.enabled
        }
      }
      return {
        tool_id: vo.tool_id,
        operation: vo.operation,
        enabled: vo.enabled
      }
    }),
    skills: {
      ...skills,
      skill_ids: (skills?.skill_ids as Skill[])?.map(vo => vo.id)
    }
  }

  return params
}
