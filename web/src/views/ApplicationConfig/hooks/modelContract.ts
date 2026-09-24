import type { Config, ModelConfig } from '../types'
import type { Modality, ModelFeature } from '@/views/ModelManagement/types'

const modalities: Modality[] = ['text', 'image', 'audio', 'video']
const features: ModelFeature[] = ['thinking', 'thinking_only', 'json_output', 'function_call']
type Legacy = { capability?: string[]; is_omni?: boolean }

/** D4 only: migrate saved app/agent data on read, never manager API responses. */
export function normalizeSavedModel<T extends ModelConfig>(value: T & Legacy) {
  const { capability = [], is_omni, ...rest } = value
  const inputs = value.input_modalities ?? capability.map(v => v === 'vision' ? 'image' : v)
  return {
    ...rest,
    input_modalities: [...new Set<Modality>(['text', ...inputs.filter((v): v is Modality => modalities.includes(v as Modality))])],
    output_modalities: [...new Set<Modality>(value.output_modalities ?? (is_omni ? ['text', 'audio'] : ['text']))],
    features: value.features ?? capability.filter((v): v is ModelFeature => features.includes(v as ModelFeature)),
  }
}

export function normalizeSavedAgent(value: Config & Legacy): Config {
  const { capability, is_omni, ...rest } = value
  const model = normalizeSavedModel({
    capability, is_omni,
    input_modalities: value.input_modalities,
    output_modalities: value.output_modalities,
    ...value.model_parameters,
  })
  return {
    ...rest,
    input_modalities: value.input_modalities ?? model.input_modalities,
    output_modalities: value.output_modalities ?? model.output_modalities,
    model_parameters: model,
    // Application feature settings are NOT model feature tags.
    features: value.features,
  }
}
