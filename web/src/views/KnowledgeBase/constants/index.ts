import type { Model } from '@/views/ModelManagement/types';

export const baseModelFields = [
  { name: 'llm', type: 'llm', label: 'llmModel', required: true },
  { name: 'embedding', type: 'embedding', label: 'embeddingModel', required: true },
  { name: 'reranker', type: 'rerank', label: 'rerankModel', required: true },
] as const satisfies readonly {
  name: string;
  type: Model['type'];
  label: string;
  required: boolean;
}[];
export const multimodalModelFields = [
  { name: 'image2text', label: 'visionModel', type: 'vision', required: false },
  { name: 'audio2text', label: 'audioModel', type: 'audio', required: false },
  { name: 'video2text', label: 'videoModel', type: 'video', required: false },
] as const satisfies readonly {
  name: string;
  type: Model['type'];
  label: string;
  required: boolean;
}[];