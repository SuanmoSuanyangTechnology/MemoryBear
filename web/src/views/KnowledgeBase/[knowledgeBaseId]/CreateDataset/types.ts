import type { ParentChildBlockConfigValues } from './ParentChildBlockConfig';

export type SourceType = 'local' | 'link' | 'text' | 'csv';
export type ProcessingMethod = 'directBlock' | 'qaExtract' | 'parentChildBlock';
export type ParameterSettings = 'defaultSettings' | 'customSettings';

export const stepKeys = ['selectFile', 'parameterSettings', 'dataPreview', 'confirmUpload'] as const;
export type StepKey = (typeof stepKeys)[number];

export const stepIndexMap: Record<StepKey, number> = {
  selectFile: 0,
  parameterSettings: 1,
  dataPreview: 2,
  confirmUpload: 3,
};

export interface CreateDatasetLocationState {
  source?: SourceType;
  knowledgeBaseId?: string;
  parentId?: string;
  startStep?: StepKey;
  fileId?: string | string[];
  fileIds?: string | string[];
}

export interface CreateDatasetFormValues extends ParentChildBlockConfigValues {
  title?: string;
  content?: string;
  image?: {
    vision_enabled: boolean,
    vision_mode: 0 | 1 | 2 | string;
  },
  pdfEnhancementEnabled: boolean;
  pdfEnhancementMethod: string;
  processingMethod: ProcessingMethod;
  parameterSettings: ParameterSettings;
  delimiter: string;
  blockSize: number;
  chunkOverlap: number;
  qaPrompt?: string;
}
