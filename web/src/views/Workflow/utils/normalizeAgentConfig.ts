import type { AgentReference } from '../types'

const REFERENCE_CONFIG_KEYS = [
  'message',
  'context',
  'variable_mapping',
  'files',
  'error_handle',
] as const

export const normalizeAgentConfig = (config: Record<string, unknown>): Record<string, unknown> => {
  const mode = config.mode === 'reference' ? 'reference' : 'inline'

  if (mode === 'inline') {
    const inlineConfig = { ...config }
    delete inlineConfig.reference
    delete inlineConfig.variable_mapping
    delete inlineConfig.files
    return {
      ...inlineConfig,
      mode,
    }
  }

  const normalizedConfig: Record<string, unknown> = { mode }
  const reference = config.reference as AgentReference | undefined

  if (reference) {
    const normalizedReference: AgentReference = {
      app_id: reference.app_id,
      release_policy: reference.release_policy,
    }
    if (reference.release_policy === 'pinned') {
      normalizedReference.release_id = reference.release_id
    }
    normalizedConfig.reference = normalizedReference
  }

  REFERENCE_CONFIG_KEYS.forEach(key => {
    if (config[key] !== undefined) {
      normalizedConfig[key] = config[key]
    }
  })

  return normalizedConfig
}
