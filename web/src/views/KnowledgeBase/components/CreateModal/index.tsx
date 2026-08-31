import { forwardRef, useImperativeHandle, useState } from 'react';
import { App, Button, Form, Steps } from 'antd';
import { useTranslation } from 'react-i18next';
import {
  checkFeishuSync,
  checkYuqueSync,
  createKnowledgeBase,
  deleteKnowledgeGraph,
  getKnowledgeGraphEntityTypes,
  rebuildKnowledgeGraph,
  updateKnowledgeBase,
} from '@/api/knowledgeBase';
import RbModal from '@/components/RbModal';
import type {
  CreateModalRef,
  CreateModalRefProps,
  KnowledgeBaseFormData,
  KnowledgeBaseListItem,
} from '@/views/KnowledgeBase/types';
import CreateModalBasicConfig from './CreateModalBasicConfig';
import CreateModalKnowledgeGraphConfig from './CreateModalKnowledgeGraphConfig';
import useCreateModalModels, { MODEL_TYPE_CONFIG } from './useCreateModalModels';

const CreateModal = forwardRef<CreateModalRef, CreateModalRefProps>(({ refreshTable }, ref) => {
  const { t } = useTranslation();
  const { modal, message: messageApi } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [datasets, setDatasets] = useState<KnowledgeBaseListItem | null>(null);
  const [currentType, setCurrentType] = useState<'General' | 'Web' | 'Third-party' | 'Folder'>('General');
  const [form] = Form.useForm<KnowledgeBaseFormData>();
  const [loading, setLoading] = useState(false);
  const [currentStep, setCurrentStep] = useState(0);
  const [generatingEntityTypes, setGeneratingEntityTypes] = useState(false);
  const [isRebuildMode, setIsRebuildMode] = useState(false);
  const [originalType, setOriginalType] = useState<string>('');
  const { customModels, dynamicTypeList, getTypeList } = useCreateModalModels({
    form,
    datasets,
    visible,
  });

  const handleClose = () => {
    setDatasets(null);
    form.resetFields();
    setLoading(false);
    setCurrentStep(0);
    setIsRebuildMode(false);
    setOriginalType('');
    setVisible(false);
  };

  const generateEntityTypes = () => {
    const sceneName = form.getFieldValue(['parser_config', 'graphrag', 'scene_name']);
    if (!sceneName) {
      messageApi.error(t('knowledgeBase.enterScenarioName'));
      return;
    }

    const llmId = form.getFieldValue('llm_id');
    if (!llmId) {
      setCurrentStep(0);
      messageApi.error(t('knowledgeBase.pleaseSelectLLMModel'));
      return;
    }

    setGeneratingEntityTypes(true);
    getKnowledgeGraphEntityTypes({
      scenario: sceneName,
      llm_id: llmId,
    })
      .then((response) => {
        console.log('API Response:', response);

        if (response && typeof response === 'string' && response.trim()) {
          const entityTypesString = response.replace(/,\s*/g, '\n');
          console.log('Converted entity types:', entityTypesString);
          const currentGraphrag = form.getFieldValue(['parser_config', 'graphrag']) || {};
          const updatedGraphrag = {
            ...currentGraphrag,
            entity_types: entityTypesString,
          };
          console.log('Updating form with:', updatedGraphrag);
          form.setFieldValue(['parser_config', 'graphrag', 'entity_types'], entityTypesString);
          form.validateFields([['parser_config', 'graphrag', 'entity_types']]);
          setTimeout(() => {
            form.setFieldValue(['parser_config', 'graphrag', 'entity_types'], entityTypesString);
          }, 100);
          messageApi.success(t('knowledgeBase.generateEntityTypesSuccess'));
        } else {
          messageApi.error(`${t('knowledgeBase.generateEntityTypesFailed')}: ${t('knowledgeBase.unknownError')}`);
        }
      })
      .catch((error) => {
        console.error(`${t('knowledgeBase.generateEntityTypesFailed')}:`, error);
      })
      .finally(() => {
        setGeneratingEntityTypes(false);
      });
  };

  const setBaseFields = (record: KnowledgeBaseListItem | null, type?: string) => {
    if (!record) {
      form.resetFields();
      const defaults: Partial<KnowledgeBaseFormData> = {
        permission_id: 'Private',
        type: type || currentType,
      };
      form.setFieldsValue(defaults);
      return;
    }

    const baseValues: Partial<KnowledgeBaseFormData> = {
      name: record.name,
      description: record.description,
      permission_id: record.permission_id || 'Private',
      type: type || record.type || currentType,
      status: record.status,
      parser_config: {
        ...record.parser_config,
        graphrag: {
          use_graphrag: false,
          scene_name: '',
          entity_types: [] as any,
          method: 'general',
          resolution: false,
          community: false,
          ...(record.parser_config?.graphrag || {}),
        },
      },
    };

    if (baseValues.parser_config?.graphrag?.entity_types) {
      if (Array.isArray(baseValues.parser_config.graphrag.entity_types)) {
        (baseValues.parser_config.graphrag as any).entity_types = baseValues.parser_config.graphrag.entity_types.join('\n');
      } else if (typeof baseValues.parser_config.graphrag.entity_types === 'string') {
        (baseValues.parser_config.graphrag as any).entity_types = (
          baseValues.parser_config.graphrag.entity_types as string
        ).replace(/,\s*/g, '\n');
      }
    }

    form.setFieldsValue(baseValues);
  };

  const handleOpen = (record?: KnowledgeBaseListItem | null, type?: string) => {
    setDatasets(record || null);
    const isRebuild = type === 'rebuild';
    const actualType = isRebuild
      ? record?.type || 'General'
      : type || record?.type || currentType;

    setCurrentType(actualType as any);
    setIsRebuildMode(isRebuild);
    setOriginalType(type || '');
    setCurrentStep(isRebuild ? 1 : 0);
    setBaseFields(record || null, actualType);
    getTypeList();
    setVisible(true);
  };

  const performSave = () => {
    form.validateFields()
      .then(() => {
        setLoading(true);
        const formValues = form.getFieldsValue(true);
        const save = () => {
          if (formValues.parser_config?.graphrag?.entity_types) {
            const entityTypesString = formValues.parser_config.graphrag.entity_types as any as string;
            formValues.parser_config.graphrag.entity_types = entityTypesString
              .split('\n')
              .map((item: string) => item.trim())
              .filter((item: string) => item.length > 0);
          }

          const saveType = originalType === 'rebuild' ? currentType : formValues.type || currentType;
          const payload: KnowledgeBaseFormData = {
            ...formValues,
            type: saveType,
            permission_id: formValues.permission_id || 'Private',
            parent_id: datasets?.parent_id || undefined,
          };
          console.log('Saving payload:', payload);
          const submit = datasets?.id
            ? updateKnowledgeBase(datasets.id, payload)
            : createKnowledgeBase(payload);

          return submit.then(() => {
            if (refreshTable) {
              refreshTable();
            }
            handleClose();
          });
        };
        const shouldCheckAuth = !datasets?.id || currentStep === 0;

        if (!shouldCheckAuth || (formValues.type !== 'Third-party' && currentType !== 'Third-party')) {
          return save();
        }

        const platform = formValues.parser_config?._third_party_platform || 'yuque';
        if (platform === 'yuque') {
          const yuqueParams = {
            yuque_user_id: formValues.parser_config?.yuque_user_id,
            yuque_token: formValues.parser_config?.yuque_token,
          };
          if (!yuqueParams.yuque_user_id || !yuqueParams.yuque_token) {
            messageApi.error(t('knowledgeBase.yuqueAuthRequired'));
            setLoading(false);
            return Promise.reject(new Error('Yuque authentication is required'));
          }
          return checkYuqueSync(yuqueParams)
            .then(() => {
              messageApi.success(t('knowledgeBase.yuqueAuthSuccess'));
              return save();
            })
            .catch((error) => {
              console.error('Authentication failed:', error);
              messageApi.error(t('knowledgeBase.authFailed'));
              setLoading(false);
              return Promise.reject(error);
            });
        }

        if (platform === 'feishu') {
          const feishuParams = {
            feishu_app_id: formValues.parser_config?.feishu_app_id,
            feishu_app_secret: formValues.parser_config?.feishu_app_secret,
            feishu_folder_token: formValues.parser_config?.feishu_folder_token,
          };
          if (!feishuParams.feishu_app_id || !feishuParams.feishu_app_secret || !feishuParams.feishu_folder_token) {
            messageApi.error(t('knowledgeBase.feishuAuthRequired'));
            setLoading(false);
            return Promise.reject(new Error('Feishu authentication is required'));
          }
          return checkFeishuSync(feishuParams)
            .then(() => {
              messageApi.success(t('knowledgeBase.feishuAuthSuccess'));
              return save();
            })
            .catch((error) => {
              console.error('Authentication failed:', error);
              messageApi.error(t('knowledgeBase.authFailed'));
              setLoading(false);
              return Promise.reject(error);
            });
        }

        return save();
      })
      .catch((error) => {
        console.log('Validation or save failed:', error);
        setLoading(false);
      });
  };

  const handleSave = () => {
    const currentFormValues = form.getFieldsValue();
    const isGraphragEnabled = currentFormValues?.parser_config?.graphrag?.use_graphrag || false;

    if (originalType === 'rebuild' && isGraphragEnabled) {
      modal.confirm({
        title: t('knowledgeBase.rebuildConfirmTitle'),
        content: t('knowledgeBase.rebuildConfirmContent'),
        onOk: () => {
          deleteKnowledgeGraph(datasets?.id || '')
            .then(() => {
              console.log(t('knowledgeBase.deleteGraphSuccess'));
              return rebuildKnowledgeGraph(datasets?.id || '');
            })
            .catch(() => {
              messageApi.error(t('knowledgeBase.deleteGraphFailed'));
            });
          performSave();
        },
        onCancel: () => {},
      });
    } else {
      performSave();
    }
  };

  const handleChange = (_value: string, type: string) => {
    if (datasets?.id && type.toLowerCase() === 'embedding') {
      const fieldKey = MODEL_TYPE_CONFIG[type.toLowerCase()].fieldKey;
      const previousValue = (datasets as any)[fieldKey];
      modal.confirm({
        title: t('common.updateWarning'),
        content: t('knowledgeBase.updateEmbeddingContent'),
        onOk: () => {},
        onCancel: () => {
          form.setFieldsValue({ [fieldKey]: previousValue } as any);
        },
      });
    }
  };

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose,
  }));

  const getTitle = () => {
    if (isRebuildMode) {
      return `${t('knowledgeBase.rebuildGraph')} - ${datasets?.name || ''}`;
    }
    if (datasets?.id) {
      return `${t('knowledgeBase.edit')} ${datasets.name}`;
    }
    if (currentType === 'Folder') {
      return `${t('knowledgeBase.createA')} ${t('knowledgeBase.folder')}`;
    }
    return `${t('knowledgeBase.createA')} ${t('knowledgeBase.knowledgeBase')}`;
  };

  const isFolder = currentType === 'Folder';
  const stepItems = [
    {
      title: t('knowledgeBase.basicConfig'),
      content: (
        <CreateModalBasicConfig
          isEditing={!!datasets?.id}
          currentType={currentType}
          dynamicTypeList={dynamicTypeList}
          customModels={customModels}
          onModelChange={handleChange}
        />
      ),
    },
    ...(!isFolder ? [{
      title: t('knowledgeBase.knowledgeGraph'),
      content: (
        <CreateModalKnowledgeGraphConfig
          generatingEntityTypes={generatingEntityTypes}
          onGenerateEntityTypes={generateEntityTypes}
        />
      ),
    }] : []),
  ];

  const handleStepSave = () => {
    if (isFolder || currentStep === 1) {
      handleSave();
      return;
    }

    form.validateFields().then(() => {
      setCurrentStep(1);
    });
  };

  return (
    <RbModal
      title={getTitle()}
      open={visible}
      onCancel={handleClose}
      onOk={handleStepSave}
      footer={[
        <Button
          key="cancel"
          onClick={currentStep === 0 ? handleClose : () => setCurrentStep(0)}
        >
          {t(currentStep === 0 ? 'common.cancel' : 'common.prevStep')}
        </Button>,
        <Button
          key="submit"
          type="primary"
          loading={loading}
          onClick={handleStepSave}
        >
          {t(
            isFolder || currentStep === 1
              ? (datasets?.id ? 'common.save' : 'common.create')
              : 'common.nextStep',
          )}
        </Button>,
      ]}
      confirmLoading={loading}
    >
      {!isFolder && (
        <Steps
          size="small"
          current={currentStep}
          items={stepItems.map(({ title }) => ({ title }))}
          className="rb:mb-6!"
        />
      )}
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          permission_id: 'Private',
          type: currentType,
          parser_config: {
            graphrag: {
              use_graphrag: false,
              scene_name: '',
              entity_types: '' as any,
              method: 'general',
              resolution: false,
              community: false,
            },
          },
        }}
      >
        {stepItems[currentStep].content}
      </Form>
    </RbModal>
  );
});

export default CreateModal;
