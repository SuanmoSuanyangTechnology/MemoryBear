import { forwardRef, useEffect, useImperativeHandle, useMemo, useState } from 'react';
import { Form, Input, Select, Modal, Tabs, Switch, Radio, Button,message } from 'antd';
import { useTranslation } from 'react-i18next';
import type { KnowledgeBaseListItem, KnowledgeBaseFormData, CreateModalRef, CreateModalRefProps } from '@/views/KnowledgeBase/types';
import { 
  getModelTypeList, 
  getModelList, 
  createKnowledgeBase, 
  updateKnowledgeBase,
  getKnowledgeGraphEntityTypes
} from '@/api/knowledgeBase'
import RbModal from '@/components/RbModal'
const { TextArea } = Input;
const { confirm } = Modal

// 全局模型数据常量
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
  const [form] = Form.useForm<KnowledgeBaseFormData>();
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('basic');
  const [generatingEntityTypes, setGeneratingEntityTypes] = useState(false);
  
  // 监听 parser_config.graphrag 相关字段的变化
  const parserConfig = Form.useWatch('parser_config', form);
  const graphragConfig = parserConfig?.graphrag;
  const enableKnowledgeGraph = graphragConfig?.use_graphrag || false;
  const entityTypes = graphragConfig?.entity_types || '';
  const entityNormalization = graphragConfig?.resolution || false;
  const communityReportGeneration = graphragConfig?.community || false;

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setDatasets(null);
    form.resetFields();
    setLoading(false);
    setActiveTab('basic');
    setVisible(false);
  };

  // 生成实体类型的函数
  const generateEntityTypes = async () => {
    const sceneName = form.getFieldValue(['parser_config', 'graphrag', 'scene_name']);
    if (!sceneName) {
      // 可以添加提示用户输入场景名称
      messageApi.error(t('knowledgeBase.enterScenarioName'));
      return;
    }

    // 检查是否选择了 LLM 模型
    const llmId = form.getFieldValue('llm_id');
    if (!llmId) {
      // 跳转到基础配置页
      setActiveTab('basic');
      messageApi.error(t('knowledgeBase.pleaseSelectLLMModel'));
      return;
    }
    
    setGeneratingEntityTypes(true);
    try {
      // 这里应该调用实际的API接口
      // const user = JSON.parse(localStorage.getItem('user') as any);
      //datasets?.id || datasets?.parent_id || user?.current_workspace_id, 
      const params = {
        scenario: sceneName,
        llm_id: llmId
      };
      const response = await getKnowledgeGraphEntityTypes(params);
      // 模拟API调用
      // await new Promise(resolve => setTimeout(resolve, 1000));
      
      // 处理API响应数据
      console.log('API Response:', response); // 调试日志
      
      // 检查响应结构 - API直接返回字符串
      if (response && typeof response === 'string' && response.trim()) {
        // 将逗号分隔的字符串转换为换行分隔的格式以便在TextArea中显示
        const entityTypesString = response.replace(/,\s*/g, '\n');
        console.log('Converted entity types:', entityTypesString); // 调试日志
        
        const currentGraphrag = form.getFieldValue(['parser_config', 'graphrag']) || {};
        const updatedGraphrag = {
          ...currentGraphrag,
          entity_types: entityTypesString
        };
        
        console.log('Updating form with:', updatedGraphrag); // 调试日志
        
        // 使用更直接的方式更新表单字段
        form.setFieldValue(['parser_config', 'graphrag', 'entity_types'], entityTypesString);
        
        // 强制触发表单重新渲染
        form.validateFields([['parser_config', 'graphrag', 'entity_types']]);
        
        // 额外的强制更新机制
        setTimeout(() => {
          form.setFieldValue(['parser_config', 'graphrag', 'entity_types'], entityTypesString);
        }, 100);
        
        messageApi.success(t('knowledgeBase.generateEntityTypesSuccess'));
      } else {
        messageApi.error(t('knowledgeBase.generateEntityTypesFailed') + '：' + t('knowledgeBase.unknownError'));
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
    // 如果还没有获取过全部模型数据，则获取一次
    if (!models) {
      try {
        models = await getModelList({ page: 1, pagesize: 100 });
      } catch (error) {
        console.error('Failed to fetch models:', error);
        models = { items: [] };
      }
    }

    // 从全部模型数据中过滤出需要的类型
    const typesToFetch = types.includes('llm') ? [...types, 'chat'] : types;
    const next: Record<string, { label: string; value: string }[]> = {};
    
    typesToFetch.forEach((tp) => {
      const targetType = tp === 'image2text' ? 'chat' : tp;
      const filteredModels = (models?.items || []).filter((m: any) => m.type === targetType);
      next[tp] = filteredModels.map((m: any) => ({ label: m.name, value: m.id }));
    });
    
    setModelOptionsByType(next);
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

    // 处理 parser_config 配置数据，如果没有则设置默认值
    baseValues.parser_config = record.parser_config || {
      graphrag: {
        use_graphrag: false,
        scene_name: '',
        entity_types: [] as any,
        method: 'general',
        resolution: false,
        community: false,
      }
    };

    // 如果存在 entity_types，转换为换行分隔格式用于 TextArea 显示
    if (baseValues.parser_config.graphrag.entity_types) {
      if (Array.isArray(baseValues.parser_config.graphrag.entity_types)) {
        // 如果是数组格式，转换为换行分隔字符串
        (baseValues.parser_config.graphrag as any).entity_types = baseValues.parser_config.graphrag.entity_types.join('\n');
      } else if (typeof baseValues.parser_config.graphrag.entity_types === 'string') {
        // 如果是逗号分隔字符串格式，转换为换行分隔字符串（兼容旧数据）
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
    const nextType = type || currentType;
    setCurrentType(nextType as any);
    setBaseFields(record || null, nextType);
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

  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        setLoading(true)
        const formValues = form.getFieldsValue();
        
        // 处理 entity_types 格式转换：从换行分隔字符串转换为字符串数组
        if (formValues.parser_config && formValues.parser_config.graphrag && formValues.parser_config.graphrag.entity_types) {
          const entityTypesString = formValues.parser_config.graphrag.entity_types as any as string;
          const entityTypesArray = entityTypesString
            .split('\n')
            .map((item: string) => item.trim())
            .filter((item: string) => item.length > 0);
          formValues.parser_config.graphrag.entity_types = entityTypesArray;
        }
        
        const payload: KnowledgeBaseFormData = {
          ...formValues,
          type: formValues.type || currentType,
          permission_id: formValues.permission_id || 'Private',
          parent_id: datasets?.parent_id || undefined,
        };
        
        console.log('Saving payload:', payload); // 调试日志
        
        const submit = datasets?.id
          ? updateKnowledgeBase(datasets.id, payload)
          : createKnowledgeBase(payload);
        submit
          .then(() => {
            if (refreshTable) {
              refreshTable();
            }
            handleClose();
          })
          .catch(() => {
            setLoading(false);
          });

      }).catch((err) => {
        console.log('Validation failed:', err)
      });
  }
  const handleChange = (_value: string, tp: string) => {
    // 只在编辑模式且类型为 embedding 时触发提示
    if (datasets?.id && tp.toLowerCase() === 'embedding') {
      const fieldKey = typeToFieldKey(tp);
      // 从原始 datasets 对象中获取之前的值
      const previousValue = (datasets as any)[fieldKey];
      
      confirm({
        title: t('common.updateWarning'),
        content: t('knowledgeBase.updateEmbeddingContent'),
        onOk: () => {
          // 确定时什么也不做，保持新值
        },
        onCancel: () => {
          // 取消时恢复之前的值
          form.setFieldsValue({ [fieldKey]: previousValue } as any);
        },
      });
    }
  }
  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  // 根据 type 获取标题
  const getTitle = () => {
    if (datasets?.id) {
      return t('knowledgeBase.edit') + ' ' + datasets.name;
    }
    if (currentType === 'Folder') {
      return t('knowledgeBase.createA') + ' ' + t('knowledgeBase.folder');
    }
    return t('knowledgeBase.createA') + ' ' + t('knowledgeBase.knowledgeBase');
  };

  const dynamicTypeList = useMemo(() => modelTypeList.filter((tp) => (modelOptionsByType[tp] || []).length), [modelTypeList, modelOptionsByType]);

  // 基础配置表单内容
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

      {currentType !== 'Folder' && dynamicTypeList.map((tp) => {
        const fieldKey = typeToFieldKey(tp);
        // 当 tp 为 'llm' 时，合并 llm 和 chat 的选项
        const options = tp.toLowerCase() === 'llm' 
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

  // 知识图谱配置表单内容
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
          {/* 场景名称 */}
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
          

          {/* 实体类型 */}
          <Form.Item
            name={['parser_config', 'graphrag', 'entity_types']}
            label={t('knowledgeBase.entityTypes')}
          >
            <TextArea 
              rows={4} 
              placeholder={t('knowledgeBase.entityTypesPlaceholder')} 
            />
          </Form.Item>

          {/* 实体归一化 */}
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
         

          {/* 实体方法 */}
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

          {/* 社区报告生成 */}
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

  // Tabs 配置
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
          permission_id: 'Private', // 设置 permission_id 的默认值
          type: currentType,
          parser_config: {
            graphrag: {
              use_graphrag: false, // 默认不启用知识图谱
              scene_name: '', // 场景名称
              entity_types: '' as any, // 实体类型（界面上显示为字符串，保存时转为数组）
              method: 'general', // 默认使用通用方法
              resolution: false, // 默认不启用实体归一化
              community: false, // 默认不生成社区报告
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