import { useEffect, useState } from 'react';
import type { FormInstance } from 'antd';
import { getCustomWorkspaceModels, getWorkspaceModels } from '@/api/workspaces';
import type { KnowledgeBaseFormData, KnowledgeBaseListItem } from '@/views/KnowledgeBase/types';
import type { Model } from '@/views/ModelManagement/types';
import { baseModelFields, multimodalModelFields } from '../../constants'

interface UseCreateModalModelsOptions {
  form: FormInstance<KnowledgeBaseFormData>;
  datasets: KnowledgeBaseListItem | null;
  visible: boolean;
}

const useCreateModalModels = ({ form, datasets, visible }: UseCreateModalModelsOptions) => {
  const [customModels, setCustomModels] = useState<Record<string, Model[]>>({});
  const [workspaceModels, setWorkspaceModels] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!visible || !Object.keys(customModels).length) return;

    if (datasets?.id) {
      const dynamicValues: Record<string, string> = {};
      const source = datasets as unknown as Record<string, unknown>;
      [...baseModelFields, ...multimodalModelFields].forEach((item) => {
      const fieldKey = `${item.name}_id`;
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
    [...baseModelFields, ...multimodalModelFields].forEach((item) => {
      const { type } = item;
      const fieldKey = `${item.name}_id`;
      const workspaceModelId = workspaceModels[type];
      const options = customModels[type]
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
  }, [customModels, datasets, form, visible, workspaceModels]);

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
    getTypeList,
    resetModelInfo,
  };
};

export default useCreateModalModels;
