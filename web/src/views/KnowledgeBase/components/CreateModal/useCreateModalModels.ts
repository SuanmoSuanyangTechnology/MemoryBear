import { useEffect, useMemo, useState } from 'react';
import type { FormInstance } from 'antd';
import { getCustomWorkspaceModels, getWorkspaceModels } from '@/api/workspaces';
import type { KnowledgeBaseFormData, KnowledgeBaseListItem } from '@/views/KnowledgeBase/types';
import type { Model } from '@/views/ModelManagement/types';

interface ModelTypeConfig {
  fieldKey: string;
  modelType: string;
}

interface UseCreateModalModelsOptions {
  form: FormInstance<KnowledgeBaseFormData>;
  datasets: KnowledgeBaseListItem | null;
  visible: boolean;
}

export const MODEL_TYPE_CONFIG: Record<string, ModelTypeConfig> = {
  embedding: { fieldKey: 'embedding_id', modelType: 'embedding' },
  llm: { fieldKey: 'llm_id', modelType: 'llm' },
  image2text: { fieldKey: 'image2text_id', modelType: 'vision' },
  rerank: { fieldKey: 'reranker_id', modelType: 'rerank' },
  reranker: { fieldKey: 'reranker_id', modelType: 'rerank' },
  chat: { fieldKey: 'chat_id', modelType: 'chat' },
};

const WORKSPACE_MODEL_FIELD_BY_FORM_FIELD: Record<string, string> = {
  llm_id: 'llm',
  embedding_id: 'embedding',
  reranker_id: 'rerank',
  image2text_id: 'vision',
  chat_id: 'chat',
};

const useCreateModalModels = ({ form, datasets, visible }: UseCreateModalModelsOptions) => {
  const [customModels, setCustomModels] = useState<Record<string, Model[]>>({});
  const [workspaceModels, setWorkspaceModels] = useState<Record<string, string>>({});

  const modelTypeList = useMemo(
    () => [...new Set(
      Object.keys(customModels)
        .filter((type) => !['chat', 'video', 'audio'].includes(type))
        .map((type) => type === 'vision' ? 'image2text' : type),
    )],
    [customModels],
  );

  const modelOptionsByType = useMemo(() => {
    const options: Record<string, Model[]> = {};
    const typesToFetch = modelTypeList.includes('llm') ? [...modelTypeList, 'chat'] : modelTypeList;

    typesToFetch.forEach((type) => {
      const targetType = type === 'image2text'
        ? 'vision'
        : type === 'reranker'
          ? 'rerank'
          : type;
      options[type] = (customModels[targetType] || []).map((model) => ({
        ...model,
        disabled: model.is_deprecated,
      }));
    });

    return options;
  }, [customModels, modelTypeList]);

  useEffect(() => {
    if (!visible || !modelTypeList.length) return;

    if (datasets?.id) {
      const dynamicValues: Record<string, string> = {};
      const source = datasets as unknown as Record<string, unknown>;
      modelTypeList.forEach((type) => {
        const normalizedType = type.toLowerCase();
        const fieldKey = MODEL_TYPE_CONFIG[normalizedType]?.fieldKey || `${normalizedType}_id`;
        const fieldValue = source[fieldKey];
        if (typeof fieldValue === 'string') {
          dynamicValues[fieldKey] = fieldValue;
        }
      });

      if (Object.keys(dynamicValues).length) {
        form.setFieldsValue(dynamicValues as Partial<KnowledgeBaseFormData>);
      }
      return;
    }

    const defaultValues: Record<string, string> = {};
    modelTypeList.forEach((type) => {
      const normalizedType = type.toLowerCase();
      const fieldKey = MODEL_TYPE_CONFIG[normalizedType]?.fieldKey || `${normalizedType}_id`;
      const workspaceField = WORKSPACE_MODEL_FIELD_BY_FORM_FIELD[fieldKey];
      const workspaceModelId = workspaceField ? workspaceModels[workspaceField] : undefined;
      const options = (normalizedType === 'llm'
        ? [...(modelOptionsByType.llm || []), ...(modelOptionsByType.chat || [])]
        : modelOptionsByType[type] || []);

      const workspaceModel = workspaceModelId
        ? options.find((model) => model.id === workspaceModelId || model.model_id === workspaceModelId)
        : undefined;
      const defaultModelId = workspaceModel?.id || options[0]?.id;

      if (defaultModelId) {
        defaultValues[fieldKey] = defaultModelId;
      }
    });

    if (Object.keys(defaultValues).length) {
      form.setFieldsValue(defaultValues as any);
    }
  }, [customModels, datasets, form, modelOptionsByType, modelTypeList, visible, workspaceModels]);

  const dynamicTypeList = useMemo(
    () => modelTypeList.filter((type) => (modelOptionsByType[type] || []).length),
    [modelOptionsByType, modelTypeList],
  );

  const getTypeList = () => {
    Promise.all([getCustomWorkspaceModels(), getWorkspaceModels()])
      .then(([modelsResponse, workspaceResponse]) => {
        setCustomModels((modelsResponse || {}) as Record<string, Model[]>);
        setWorkspaceModels((workspaceResponse || {}) as Record<string, string>);
      })
      .catch((error) => {
        console.error('Failed to fetch knowledge base models:', error);
        setCustomModels({});
        setWorkspaceModels({});
      });
  };
  const resetModelInfo = () => {
    setCustomModels({});
    setWorkspaceModels({});
  };



  return {
    customModels,
    dynamicTypeList,
    getTypeList,
    resetModelInfo,
  };
};

export default useCreateModalModels;
