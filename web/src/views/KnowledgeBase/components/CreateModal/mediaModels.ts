import type { KnowledgeBaseFormData } from '@/views/KnowledgeBase/types';
import type { Model } from '@/views/ModelManagement/types';

export const OPTIONAL_MEDIA_TYPES = ['image2text', 'audio2text', 'video2text'];

export const selectMediaModels = (models: Model[], type: 'audio2text' | 'video2text') =>
  models.filter((model) => model.provider.toLowerCase() === 'dashscope'
    && model.is_active && !model.is_deprecated && model.is_available !== false
    && (type === 'audio2text'
      ? model.type === 'asr' && model.name === 'qwen3-asr-flash-filetrans'
      : ['llm', 'chat'].includes(model.type) && model.capability?.includes('video')
        && model.name === 'qwen3.5-omni-plus-2026-03-15'));

export const serializeOptionalMediaModels = (values: Partial<KnowledgeBaseFormData>) => ({
  image2text_id: values.image2text_id ?? null,
  audio2text_id: values.audio2text_id ?? null,
  video2text_id: values.video2text_id ?? null,
});
