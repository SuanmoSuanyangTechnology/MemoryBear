import { forwardRef, useEffect, useImperativeHandle, useMemo, useState } from 'react';
import { Form, Input, Select, Modal, Tabs, Switch, Radio, Button, message } from 'antd';
import { useTranslation } from 'react-i18next';
import type { KnowledgeBaseListItem, KnowledgeBaseFormData, CreateModalRef, CreateModalRefProps } from '@/views/KnowledgeBase/types';
import { 
  getModelTypeList, 
  getModelList, 
  createKnowledgeBase, 
  updateKnowledgeBase,
  getKnowledgeGraphEntityTypes,
  deleteKnowledgeGraph,
  rebuildKnowledgeGraph,
  checkFeishuSync,
  checkYuqueSync
} from '@/api/knowledgeBase'
import RbModal from '@/components/RbModal'
import SliderInput from '@/components/SliderInput'
const { TextArea } = Input;
const { confirm } = Modal

// Global model data constant
let models: any = null;

const CreateModal = forwardRef<CreateModalRef, CreateModalRefProps>(({ 
  refreshTable
}, ref) => {
  const { t } = useTranslation();
  const [messageApi, contextHolder] = message.useMessage();
  const [visible, setVisible] = useState(false);
  const [modelTypeList, setModelTypeList] = useState<string[]>([]);
  const [modelOptionsByType, setModelOptionsByType] = useState<Record<string, { label: string; value: string }[]>>({});
  const [datasets, setDatasets] = useState<KnowledgeBaseListItem | null>(null);
  const [currentType, setCurrentType] = useState<'General' | 'Web' | 'Third-party' | 'Folder'>('General');
  const [thirdPartyPlatform, setThirdPartyPlatform] = useState<'yuque' | 'feishu'>('yuque');
  const [form] = Form.useForm<KnowledgeBaseFormData>();
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('basic');
  const [generatingEntityTypes, setGeneratingEntityTypes] = useState(false);
  const [isRebuildMode, setIsRebuildMode] = useState(false);
  const [originalType, setOriginalType] = useState<string>(''); // Save original type parameter
  
  // Watch for changes to parser_config.graphrag related fields
  const parserConfig = Form.useWatch('parser_config', form);
  const graphragConfig = parserConfig?.graphrag;
  const enableKnowledgeGraph = graphragConfig?.use_graphrag || false;
  const entityTypes = graphragConfig?.entity_types || '';
  const entityNormalization = graphragConfig?.resolution || false;
  const communityReportGeneration = graphragConfig?.community || false;

  // Encapsulate cancel method, add close modal logic
  const handleClose = () => {
    setDatasets(null);
    form.resetFields();
    setLoading(false);
    setActiveTab('basic');
    setIsRebuildMode(false); // Reset rebuild mode flag
    setOriginalType(''); // Reset original type
    setThirdPartyPlatform('yuque'); // Reset third party platform
    setVisible(false);
  };

  // Generate entity types function
  const generateEntityTypes = async () => {
    const sceneName = form.getFieldValue(['parser_config', 'graphrag', 'scene_name']);
    if (!sceneName) {
      // Can add prompt for user to enter scenario name
      messageApi.error(t('knowledgeBase.enterScenarioName'));
      return;
    }

    // Check if LLM model is selected
    const llmId = form.getFieldValue('llm_id');
    if (!llmId) {
      // Navigate to basic configuration page
      setActiveTab('basic');
      messageApi.error(t('knowledgeBase.pleaseSelectLLMModel'));
      return;
    }
    
    setGeneratingEntityTypes(true);
    try {
      // Call the actual API interface here
      // const user = JSON.parse(localStorage.getItem('user') as any);
      //datasets?.id || datasets?.parent_id || user?.current_workspace_id, 
      const params = {
        scenario: sceneName,
        llm_id: llmId
      };
      const response = await getKnowledgeGraphEntityTypes(params);
      // Simulate API call
      // await new Promise(resolve => setTimeout(resolve, 1000));
      
      // Process API response data
      console.log('API Response:', response); // Debug log
      
      // Check response structure - API returns string directly
      if (response && typeof response === 'string' && response.trim()) {
        // Convert comma-separated string to newline-separated format for TextArea display
        const entityTypesString = response.replace(/,\s*/g, '\n');
        console.log('Converted entity types:', entityTypesString); // Debug log
        
        const currentGraphrag = form.getFieldValue(['parser_config', 'graphrag']) || {};
        const updatedGraphrag = {
          ...currentGraphrag,
          entity_types: entityTypesString
        };
        
        console.log('Updating form with:', updatedGraphrag); // Debug log
        
        // Use more direct way to update form field
        form.setFieldValue(['parser_config', 'graphrag', 'entity_types'], entityTypesString);
        
        // Force trigger form re-render
        form.validateFields([['parser_config', 'graphrag', 'entity_types']]);
        
        // Additional forced update mechanism
        setTimeout(() => {
          form.setFieldValue(['parser_config', 'graphrag', 'entity_types'], entityTypesString);
        }, 100);
        
        messageApi.success(t('knowledgeBase.generateEntityTypesSuccess'));
      } else {
        messageApi.error(t('knowledgeBase.generateEntityTypesFailed') + ': ' + t('knowledgeBase.unknownError'));
      }
    } catch (error) {
      console.error(t('knowledgeBase.generateEntityTypesFailed') + ':', error);
    } finally {
      setGeneratingEntityTypes(false);
    }
  };

  const typeToFieldKey = (type: string): string => {
    switch ((type || '').toLowerCase()) {
      case 'embedding':
        return 'embedding_id';
      case 'llm':
        return 'llm_id';
      case 'image2text':
        return 'image2text_id';
      case 'rerank':
      case 'reranker':
        return 'reranker_id';
      case 'chat':
        return 'chat_id';
      default:
        return `${type.toLowerCase()}_id`;
    }
  };

  const fetchModelLists = async (types: string[]) => {
    // If model data hasn't been fetched yet, fetch it once
    if (!models) {
      try {
        models = await getModelList({ page: 1, pagesize: 100 });
      } catch (error) {
        console.error('Failed to fetch models:', error);
        models = { items: [] };
      }
    }

    // Filter out the required types from all model data
    const typesToFetch = types.includes('llm') ? [...types, 'chat'] : types;
    const next: Record<string, { label: string; value: string }[]> = {};
    
    typesToFetch.forEach((tp) => {
      const targetType = tp === 'image2text' ? 'chat' : tp;
      const filteredModels = (models?.items || []).filter((m: any) => m.type === targetType);
      next[tp] = filteredModels.map((m: any) => ({ label: m.name, value: m.id }));
    });
    
    setModelOptionsByType(next);

    // If not in edit mode, set default value to first item for each type dropdown
    if (!datasets?.id) {
      const defaultValues: Record<string, string> = {};
      types.forEach((tp) => {
        const fieldKey = typeToFieldKey(tp);
        const options = tp.toLowerCase() === 'llm' 
          ? [...(next['llm'] || []), ...(next['chat'] || [])]
          : next[tp] || [];
        
        // If there are options and current field has no value, set first option as default
        if (options.length > 0 && !form.getFieldValue(fieldKey as any)) {
          defaultValues[fieldKey] = options[0].value;
        }
      });
      
      if (Object.keys(defaultValues).length > 0) {
        form.setFieldsValue(defaultValues as any);
      }
    }
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
    };

    // Process parser_config data, set default values if not present
    const recordAny = record as any;
    baseValues.parser_config = {
      ...record.parser_config,
      graphrag: {
        use_graphrag: false,
        scene_name: '',
        entity_types: [] as any,
        method: 'general',
        resolution: false,
        community: false,
        ...(record.parser_config?.graphrag || {})
      }
    };

    // Add Third-party specific fields to parser_config if exists
    if (recordAny.parser_config?.third_party_platform) {
      baseValues.parser_config.third_party_platform = recordAny.parser_config.third_party_platform;
    }
    if (recordAny.parser_config?.yuque_user_id) {
      baseValues.parser_config.yuque_user_id = recordAny.parser_config.yuque_user_id;
    }
    if (recordAny.parser_config?.yuque_token) {
      baseValues.parser_config.yuque_token = recordAny.parser_config.yuque_token;
    }
    if (recordAny.parser_config?.app_id) {
      baseValues.parser_config.app_id = recordAny.parser_config.app_id;
    }
    if (recordAny.parser_config?.app_secret) {
      baseValues.parser_config.app_secret = recordAny.parser_config.app_secret;
    }
    if (recordAny.parser_config?.folder_token) {
      baseValues.parser_config.folder_token = recordAny.parser_config.folder_token;
    }

    // Add Web specific fields to parser_config if exists
    if (recordAny.parser_config?.entry_url) {
      baseValues.parser_config.entry_url = recordAny.parser_config.entry_url;
    }
    if (recordAny.parser_config?.max_pages) {
      baseValues.parser_config.max_pages = recordAny.parser_config.max_pages;
    }
    if (recordAny.parser_config?.delay_seconds) {
      baseValues.parser_config.delay_seconds = recordAny.parser_config.delay_seconds;
    }
    if (recordAny.parser_config?.timeout_seconds) {
      baseValues.parser_config.timeout_seconds = recordAny.parser_config.timeout_seconds;
    }
    if (recordAny.parser_config?.user_agent) {
      baseValues.parser_config.user_agent = recordAny.parser_config.user_agent;
    }

    // If entity_types exists, convert to newline-separated format for TextArea display
    if (baseValues.parser_config.graphrag.entity_types) {
      if (Array.isArray(baseValues.parser_config.graphrag.entity_types)) {
        // If array format, convert to newline-separated string
        (baseValues.parser_config.graphrag as any).entity_types = baseValues.parser_config.graphrag.entity_types.join('\n');
      } else if (typeof baseValues.parser_config.graphrag.entity_types === 'string') {
        // If comma-separated string format, convert to newline-separated string (compatible with old data)
        (baseValues.parser_config.graphrag as any).entity_types = (baseValues.parser_config.graphrag.entity_types as string).replace(/,\s*/g, '\n');
      }
    }

    form.setFieldsValue(baseValues);
  };

  const setDynamicModelFields = (record: KnowledgeBaseListItem | null, types: string[]) => {
    if (!record || !types.length) return;
    const dynamicValues: Record<string, string | undefined> = {};
    const source = record as unknown as Record<string, unknown>;
    types.forEach((tp) => {
      const fieldKey = typeToFieldKey(tp);
      const fieldValue = source[fieldKey];
      if (typeof fieldValue === 'string') {
        dynamicValues[fieldKey] = fieldValue;
      }
    });
    if (Object.keys(dynamicValues).length) {
      form.setFieldsValue(dynamicValues as Partial<KnowledgeBaseFormData>);
    }
  };

  const handleOpen = (record?: KnowledgeBaseListItem | null, type?: string) => {
    setDatasets(record || null);
    
    // If rebuild mode, use record's actual type, otherwise use passed type
    const actualType = type === 'rebuild' ? (record?.type || 'General') : (type || currentType);
    setCurrentType(actualType as any);
    setIsRebuildMode(type === 'rebuild'); // Set rebuild mode flag
    setOriginalType(type || ''); // Save original type parameter
    
    // Set third party platform if editing Third-party type
    if (actualType === 'Third-party' && record) {
      const platform = (record as any).parser_config?.third_party_platform;
      if (platform === 'yuque' || platform === 'feishu') {
        setThirdPartyPlatform(platform);
      }
    } else {
      setThirdPartyPlatform('yuque'); // Reset to default
    }
    
    // If rebuild mode, default to knowledge graph tab
    if (type === 'rebuild') {
      setActiveTab('knowledgeGraph');
    } else {
      setActiveTab('basic');
    }
    
    setBaseFields(record || null, actualType);
    getTypeList(record || null);
    setVisible(true);
  };

  const getTypeList = async (record: KnowledgeBaseListItem | null) => {
    const response = await getModelTypeList();
    const types = Array.isArray(response) ? [...response.filter(type => type !== 'chat'),'image2text'] : [];
    setModelTypeList(types);
    if (types.length) {
      await fetchModelLists(types);
      setDynamicModelFields(record, types);
    } else {
      setModelOptionsByType({});
    }
  };

  useEffect(() => {
    if (!visible) return;
    setBaseFields(datasets, currentType);
    setDynamicModelFields(datasets, modelTypeList);
  }, [visible, datasets, currentType, modelTypeList]);

  // Encapsulate save method, add submit logic
  const handleSave = () => {
    // Get current knowledge graph enabled status from form
    const currentFormValues = form.getFieldsValue();
    const isGraphragEnabled = currentFormValues?.parser_config?.graphrag?.use_graphrag || false;
    
    // If original type is 'rebuild' and knowledge graph is enabled, show confirmation dialog
    if (originalType === 'rebuild' && isGraphragEnabled) {
      confirm({
        title: t('knowledgeBase.rebuildConfirmTitle'),
        content: t('knowledgeBase.rebuildConfirmContent'),
        onOk: async() => {
          handleDeleteGraph()
          performSave();
          await rebuildKnowledgeGraph(datasets?.id || '')
        },
        onCancel: () => {
          // User cancelled, no action taken
        },
      });
    } else {
      // Non-rebuild mode or knowledge graph not enabled, save directly
      performSave();
    }
  };
  const handleDeleteGraph = () => {
     try{
        deleteKnowledgeGraph(datasets?.id || '')
        console.log(t('knowledgeBase.deleteGraphSuccess'))
     }catch(e){
        messageApi.error(t('knowledgeBase.deleteGraphFailed'))
     }
  };
  // Actual save logic
  const performSave = async () => {
    try {
      await form.validateFields();
      setLoading(true);
      const formValues = form.getFieldsValue();
      
      // Check Third-party authentication before saving
      if (formValues.type === 'Third-party' || currentType === 'Third-party') {
        const platform = formValues.parser_config?.third_party_platform || thirdPartyPlatform;
        
        try {
          if (platform === 'yuque') {
            // Validate Yuque credentials
            const yuqueParams = {
              yuque_user_id: formValues.parser_config?.yuque_user_id,
              yuque_token: formValues.parser_config?.yuque_token
            };
            
            if (!yuqueParams.yuque_user_id || !yuqueParams.yuque_token) {
              messageApi.error(t('knowledgeBase.yuqueAuthRequired'));
              setLoading(false);
              return;
            }
            
            await checkYuqueSync(yuqueParams);
            messageApi.success(t('knowledgeBase.yuqueAuthSuccess'));
            
          } else if (platform === 'feishu') {
            // Validate Feishu credentials
            const feishuParams = {
              app_id: formValues.parser_config?.app_id,
              app_secret: formValues.parser_config?.app_secret,
              folder_token: formValues.parser_config?.folder_token
            };
            
            if (!feishuParams.app_id || !feishuParams.app_secret || !feishuParams.folder_token) {
              messageApi.error(t('knowledgeBase.feishuAuthRequired'));
              setLoading(false);
              return;
            }
            
            await checkFeishuSync(feishuParams);
            messageApi.success(t('knowledgeBase.feishuAuthSuccess'));
          }
        } catch (error) {
          console.error('Authentication failed:', error);
          messageApi.error(t('knowledgeBase.authFailed'));
          setLoading(false);
          return;
        }
      }
      
      // Process entity_types format conversion: from newline-separated string to string array
      if (formValues.parser_config && formValues.parser_config.graphrag && formValues.parser_config.graphrag.entity_types) {
        const entityTypesString = formValues.parser_config.graphrag.entity_types as any as string;
        const entityTypesArray = entityTypesString
          .split('\n')
          .map((item: string) => item.trim())
          .filter((item: string) => item.length > 0);
        formValues.parser_config.graphrag.entity_types = entityTypesArray;
      }
      
      // Ensure correct type is used when saving (not 'rebuild')
      const saveType = originalType === 'rebuild' ? currentType : (formValues.type || currentType);
      
      const payload: KnowledgeBaseFormData = {
        ...formValues,
        type: saveType,
        permission_id: formValues.permission_id || 'Private',
        parent_id: datasets?.parent_id || undefined,
      };
      
      console.log('Saving payload:', payload); // Debug log
      
      const submit = datasets?.id
        ? updateKnowledgeBase(datasets.id, payload)
        : createKnowledgeBase(payload);
      
      await submit;
      
      if (refreshTable) {
        refreshTable();
      }
      handleClose();
      
    } catch (err) {
      console.log('Validation or save failed:', err);
      setLoading(false);
    }
  }
  const handleChange = (_value: string, tp: string) => {
    // Only trigger prompt in edit mode and when type is embedding
    if (datasets?.id && tp.toLowerCase() === 'embedding') {
      const fieldKey = typeToFieldKey(tp);
      // Get previous value from original datasets object
      const previousValue = (datasets as any)[fieldKey];
      
      confirm({
        title: t('common.updateWarning'),
        content: t('knowledgeBase.updateEmbeddingContent'),
        onOk: () => {
          // Do nothing on confirm, keep new value
        },
        onCancel: () => {
          // Restore previous value on cancel
          form.setFieldsValue({ [fieldKey]: previousValue } as any);
        },
      });
    }
  }
  // Methods exposed to parent component
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  // Get title based on type
  const getTitle = () => {
    if (isRebuildMode) {
      return t('knowledgeBase.rebuildGraph') + ' - ' + (datasets?.name || '');
    }
    if (datasets?.id) {
      return t('knowledgeBase.edit') + ' ' + datasets.name;
    }
    if (currentType === 'Folder') {
      return t('knowledgeBase.createA') + ' ' + t('knowledgeBase.folder');
    }
    return t('knowledgeBase.createA') + ' ' + t('knowledgeBase.knowledgeBase');
  };

  const dynamicTypeList = useMemo(() => modelTypeList.filter((tp) => (modelOptionsByType[tp] || []).length), [modelTypeList, modelOptionsByType]);

  // Basic configuration form content
  const renderBasicConfig = () => (
    <>
      {!datasets?.id && (
        <Form.Item
          name="name"
          label={t('knowledgeBase.createForm.name')}
          rules={[{ required: true, message: t('knowledgeBase.createForm.nameRequired') }]}
        >
          <Input placeholder={t('knowledgeBase.createForm.name')} />
        </Form.Item>
      )}
      <Form.Item name="description" label={t('knowledgeBase.createForm.description')}>
        <TextArea rows={2} placeholder={t('knowledgeBase.createForm.description')} />
      </Form.Item>

      {/* Web type specific fields */}
      {currentType === 'Web' && (
        <>
          <Form.Item
            name={['parser_config', 'entry_url']}
            label={t('knowledgeBase.createForm.entryUrl')}
            rules={[
              { required: true, message: t('knowledgeBase.createForm.entryUrlRequired') },
              { type: 'url', message: t('knowledgeBase.createForm.entryUrlInvalid') }
            ]}
          >
            <Input placeholder="https://ai.redbearai.com" />
          </Form.Item>

          <Form.Item
            name={['parser_config', 'max_pages']}
            label={t('knowledgeBase.createForm.maxPages')}
            rules={[{ required: true, message: t('knowledgeBase.createForm.maxPagesRequired') }]}
            initialValue={20}
          >
            <SliderInput
              min={10}
              max={200}
              step={1}
            />
          </Form.Item>

          <Form.Item
            name={['parser_config', 'delay_seconds']}
            label={t('knowledgeBase.createForm.delaySeconds')}
            rules={[{ required: true, message: t('knowledgeBase.createForm.delaySecondsRequired') }]}
            initialValue={1.0}
          >
            <SliderInput
              min={1}
              max={3}
              step={0.1}
            />
          </Form.Item>

          <Form.Item
            name={['parser_config', 'timeout_seconds']}
            label={t('knowledgeBase.createForm.timeoutSeconds')}
            rules={[{ required: true, message: t('knowledgeBase.createForm.timeoutSecondsRequired') }]}
            initialValue={10}
          >
            <SliderInput
              min={5}
              max={15}
              step={1}
            />
          </Form.Item>

          <Form.Item
            name={['parser_config', 'user_agent']}
            label={t('knowledgeBase.createForm.userAgent')}
            rules={[{ required: true, message: t('knowledgeBase.createForm.userAgentRequired') }]}
            initialValue="KnowledgeBaseCrawler/1.0"
          >
            <Input placeholder="KnowledgeBaseCrawler/1.0" />
          </Form.Item>
        </>
      )}

      {/* Third-party type specific fields */}
      {currentType === 'Third-party' && (
        <>
          <Form.Item
            name={['parser_config', 'third_party_platform']}
            label={t('knowledgeBase.createForm.platform')}
            rules={[{ required: true, message: t('knowledgeBase.createForm.platformRequired') }]}
            initialValue="yuque"
          >
            <Select
              value={thirdPartyPlatform}
              onChange={(value) => setThirdPartyPlatform(value)}
              options={[
                { value: 'yuque', label: t('knowledgeBase.createForm.yuque') },
                { value: 'feishu', label: t('knowledgeBase.createForm.feishu') }
              ]}
            />
          </Form.Item>

          {thirdPartyPlatform === 'yuque' && (
            <>
              <Form.Item
                name={['parser_config', 'yuque_user_id']}
                label={t('knowledgeBase.createForm.yuqueUserId')}
                rules={[{ required: true, message: t('knowledgeBase.createForm.yuqueUserIdRequired') }]}
              >
                <Input placeholder={t('knowledgeBase.createForm.yuqueUserIdPlaceholder')} />
              </Form.Item>

              <Form.Item
                name={['parser_config', 'yuque_token']}
                label={t('knowledgeBase.createForm.yuqueToken')}
                rules={[{ required: true, message: t('knowledgeBase.createForm.yuqueTokenRequired') }]}
              >
                <Input.Password placeholder={t('knowledgeBase.createForm.yuqueTokenPlaceholder')} />
              </Form.Item>
            </>
          )}

          {thirdPartyPlatform === 'feishu' && (
            <>
              <Form.Item
                name={['parser_config', 'app_id']}
                label={t('knowledgeBase.createForm.feishuAppId')}
                rules={[{ required: true, message: t('knowledgeBase.createForm.feishuAppIdRequired') }]}
              >
                <Input placeholder={t('knowledgeBase.createForm.feishuAppIdPlaceholder')} />
              </Form.Item>

              <Form.Item
                name={['parser_config', 'app_secret']}
                label={t('knowledgeBase.createForm.feishuAppSecret')}
                rules={[{ required: true, message: t('knowledgeBase.createForm.feishuAppSecretRequired') }]}
              >
                <Input.Password placeholder={t('knowledgeBase.createForm.feishuAppSecretPlaceholder')} />
              </Form.Item>

              <Form.Item
                name={['parser_config', 'folder_token']}
                label={t('knowledgeBase.createForm.feishuFolderToken')}
                rules={[{ required: true, message: t('knowledgeBase.createForm.feishuFolderTokenRequired') }]}
              >
                <Input placeholder={t('knowledgeBase.createForm.feishuFolderTokenPlaceholder')} />
              </Form.Item>
            </>
          )}
        </>
      )}

      {currentType !== 'Folder' && dynamicTypeList.map((tp) => {
        const fieldKey = typeToFieldKey(tp);
        // When tp is 'llm', merge llm and chat options
        const options = tp.toLowerCase() === 'llm' || tp.toLowerCase() === 'image2text'
          ? [...(modelOptionsByType['llm'] || []), ...(modelOptionsByType['chat'] || [])]
          : modelOptionsByType[tp] || [];
        return (
          <Form.Item
            key={tp}
            name={fieldKey as keyof KnowledgeBaseFormData}
            label={t(`knowledgeBase.createForm.${fieldKey}`) + ' ' + 'model'}
            rules={[{ required: true, message: t('knowledgeBase.createForm.modelRequired') }]}
          >
            <Select
              options={options}
              placeholder={t(`knowledgeBase.createForm.${fieldKey}`)}
              allowClear={false}
              showSearch
              optionFilterProp="label"
              onChange={(value) => handleChange(value, tp)}
            />
          </Form.Item>
        );
      })}
    </>
  );

  // Knowledge graph configuration form content
  const renderKnowledgeGraphConfig = () => (
    <>
      <div className={`rb:flex rb:w-full rb:items-center rb:p-4 rb:border-1 rb:rounded-lg rb:mb-4 ${
        enableKnowledgeGraph 
          ? 'rb:border-[#155EEF] rb:bg-[rgba(21,94,239,0.06)]' 
          : 'rb:border-[#EBEBEB]'
      }`}>
        <div className='rb:flex rb:flex-col rb:flex-1'>
          <div className='rb:text-[#212332] rb:text-base rb:font-medium'>
            {t('knowledgeBase.enableKnowledgeGraph')}
          </div>
          <div className='rb:text-xs rb:text-[#5B6167] rb:mt-2'>
            {t('knowledgeBase.enableKnowledgeGraphTips')}
          </div>
        </div>
        <Form.Item
          name={['parser_config', 'graphrag', 'use_graphrag']}
          label=''
          valuePropName="checked"
          className='rb:mb-0'
        >
          <Switch />
        </Form.Item>
      </div>

      {enableKnowledgeGraph && (
        <>
          <div className='rb:text-[#212332] rb:text-base rb:font-medium rb:mb-4'>
            {t('knowledgeBase.graphConfig')}
          </div>
          {/* Scene name */}
          <div className='rb:flex rb:items-center rb:gap-2'>
              <Form.Item
                name={['parser_config', 'graphrag', 'scene_name']}
                label={t('knowledgeBase.sceneName')}
                className='rb:w-full rb:min-w-[240px]'
                rules={[{ required: true, message: t('common.pleaseEnter') + t('knowledgeBase.sceneName') }]}
              >
                <Input  placeholder={t('knowledgeBase.sceneNamePlaceholder')} />
              </Form.Item>
                <Button 
                  type="primary" 
                  loading={generatingEntityTypes}
                  onClick={generateEntityTypes}
                  className='rb:mt-1'
                >
                  {!(entityTypes as any as string) || (entityTypes as any as string).trim() === '' 
                    ? t('knowledgeBase.generateEntityTypes')
                    : t('knowledgeBase.regenerateEntityTypes')
                  }
                </Button> 
          </div>
          

          {/* Entity types */}
          <Form.Item
            name={['parser_config', 'graphrag', 'entity_types']}
            label={t('knowledgeBase.entityTypes')}
          >
            <TextArea 
              rows={4} 
              placeholder={t('knowledgeBase.entityTypesPlaceholder')} 
            />
          </Form.Item>

          {/* Entity normalization */}
          <div className={`rb:flex rb:w-full rb:gap-2 rb:items-center rb:p-4 rb:border-1 rb:rounded-lg rb:mb-4 ${
            entityNormalization 
              ? 'rb:border-[#155EEF] rb:bg-[rgba(21,94,239,0.06)]' 
              : 'rb:border-[#EBEBEB]'
          }`}>
            <div className='rb:flex rb:flex-col rb:flex-1'>
              <div className='rb:text-[#212332] rb:text-base rb:font-medium'>
                {t('knowledgeBase.entityNormalization')}
              </div>
              <div className='rb:text-xs rb:text-[#5B6167] rb:mt-2'>
                {t('knowledgeBase.entityNormalizationTips')}
              </div>
            </div>
             <Form.Item
              name={['parser_config', 'graphrag', 'resolution']}
              valuePropName="checked"
              className='rb:mb-0'
            >
              <Switch />
            </Form.Item>
          </div>
         

          {/* Entity method */}
          <Form.Item
            name={['parser_config', 'graphrag', 'method']}
            label={t('knowledgeBase.entityMethod')}
            initialValue="general"
          >
            <Radio.Group>
              <Radio value="general">{t('knowledgeBase.entityMethodGeneral')}</Radio>
              <Radio value="light">{t('knowledgeBase.entityMethodLight')}</Radio>
            </Radio.Group>
          </Form.Item>

          {/* Community report generation */}
          <div className={`rb:flex rb:w-full rb:gap-2 rb:items-center rb:p-4 rb:border-1 rb:rounded-lg rb:mb-4 ${
            communityReportGeneration 
              ? 'rb:border-[#155EEF] rb:bg-[rgba(21,94,239,0.06)]' 
              : 'rb:border-[#EBEBEB]'
          }`}>
            <div className='rb:flex rb:flex-col rb:flex-1'>
              <div className='rb:text-[#212332] rb:text-base rb:font-medium'>
                {t('knowledgeBase.communityReportGeneration')}
              </div>
              <div className='rb:text-xs rb:text-[#5B6167] rb:mt-2'>
                {t('knowledgeBase.communityReportGenerationTips')}
              </div>
            </div>
            <Form.Item
              name={['parser_config', 'graphrag', 'community']}
              valuePropName="checked"
              className='rb:mb-0'
            >
              <Switch />
            </Form.Item>
          </div>
        </>
      )}
    </>
  );

  // Tabs configuration
  const tabItems = [
    {
      key: 'basic',
      label: t('knowledgeBase.basicConfig'),
      children: renderBasicConfig(),
    },
    {
      key: 'knowledgeGraph',
      label: t('knowledgeBase.knowledgeGraph'),
      children: renderKnowledgeGraphConfig(),
    },
  ];

  return (
    <RbModal
      title={getTitle()}
      open={visible}
      onCancel={handleClose}
      okText={datasets?.id ? t('common.save') : t('common.create')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          permission_id: 'Private', // Set default value for permission_id
          type: currentType,
          parser_config: {
            graphrag: {
              use_graphrag: false, // Default not to enable knowledge graph
              scene_name: '', // Scene name
              entity_types: '' as any, // Entity types (displayed as string in UI, converted to array when saving)
              method: 'general', // Default to use general method
              resolution: false, // Default not to enable entity normalization
              community: false, // Default not to generate community reports
            }
          }
        }}
      >
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={tabItems}
        />
      </Form>
      {contextHolder}
    </RbModal>
  );
});

export default CreateModal;