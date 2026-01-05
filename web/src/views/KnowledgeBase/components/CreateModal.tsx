import { forwardRef, useEffect, useImperativeHandle, useMemo, useState } from 'react';
import { Form, Input,  Select, Modal } from 'antd';
import { useTranslation } from 'react-i18next';
import type { KnowledgeBaseListItem, KnowledgeBaseFormData, CreateModalRef, CreateModalRefProps } from '@/views/KnowledgeBase/types';
import { getModelTypeList, getModelList, createKnowledgeBase, updateKnowledgeBase } from '@/api/knowledgeBase'
import RbModal from '@/components/RbModal'
const { TextArea } = Input;
const { confirm } = Modal

// 全局模型数据常量
let models: any = null;

const CreateModal = forwardRef<CreateModalRef, CreateModalRefProps>(({ 
  refreshTable
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [modelTypeList, setModelTypeList] = useState<string[]>([]);
  const [modelOptionsByType, setModelOptionsByType] = useState<Record<string, { label: string; value: string }[]>>({});
  const [datasets, setDatasets] = useState<KnowledgeBaseListItem | null>(null);
  const [currentType, setCurrentType] = useState<'General' | 'Web' | 'Third-party' | 'Folder'>('General');
  const [form] = Form.useForm<KnowledgeBaseFormData>();
  const [loading, setLoading] = useState(false)

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setDatasets(null);
    form.resetFields();
    setLoading(false)
    setVisible(false);
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
        const payload: KnowledgeBaseFormData = {
          ...formValues,
          type: formValues.type || currentType,
          permission_id: formValues.permission_id || 'Private',
          parent_id: datasets?.parent_id || undefined,
        };
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
        }}
      >
        {/* <div className="rb:text-[14px] rb:font-medium rb:text-[#5B6167] rb:mb-[16px]">{t('model.basicParameters')}</div> */}
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

      </Form>
    </RbModal>
  );
});

export default CreateModal;