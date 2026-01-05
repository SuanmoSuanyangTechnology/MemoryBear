import { forwardRef, useImperativeHandle, useState, useEffect } from 'react';
import { Form, Input, App, Select } from 'antd';
import { useTranslation } from 'react-i18next';

import type { SpaceModalData, SpaceModalRef, Space } from '../types'
import RbModal from '@/components/RbModal'
import { createWorkspace } from '@/api/workspaces'
import RadioGroupCard from '@/components/RadioGroupCard'
import { getModelListUrl, getModelList } from '@/api/models'
import CustomSelect from '@/components/CustomSelect'
import type { Model } from '@/views/ModelManagement/types'

const FormItem = Form.Item;

interface SpaceModalProps {
  refresh: () => void;
}
const types = [
  'rag',
  'neo4j',
]

const SpaceModal = forwardRef<SpaceModalRef, SpaceModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<SpaceModalData>();
  const [loading, setLoading] = useState(false)
  const [editVo, setEditVo] = useState<Space | null>(null)
  const [modelList, setModelList] = useState<Model[]>([])

  const values = Form.useWatch([], form);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setEditVo(null)
  };

  const handleOpen = (space?: Space) => {
    if (space) {
      setEditVo(space || null)
      form.setFieldsValue({
        name: space.name,
        icon: space.icon
      })
    } else {
      form.resetFields();
    }
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        setLoading(true)
        createWorkspace(values as SpaceModalData)
          .then(() => {
            setLoading(false)
            refresh()
            handleClose()
            message.success(t('common.createSuccess'))
          })
          .catch(() => {
            setLoading(false)
          });
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  useEffect(() => {
    getModels()
  }, [])
  
  const getModels = () => {
    getModelList({ type: 'llm,chat', pagesize: 100, page: 1 })
      .then(res => {
        const response = res as { items: Model[] }
        setModelList(response.items)
      })
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t(`space.${editVo?.id ? 'editSpace' : 'createSpace'}`)}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <FormItem
          name="name"
          label={t('space.spaceName')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>
        <Form.Item 
          label={t('space.llmModel')} 
          name="llm"
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <Select
            placeholder={t('common.pleaseSelect')}
            fieldNames={{
              label: 'name',
              value: 'id',
            }}
            options={modelList}
          />
        </Form.Item>
        <Form.Item 
          label={t('space.embeddingModel')} 
          name="embedding"
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <CustomSelect
            url={getModelListUrl}
            params={{ type: 'embedding', pagesize: 100 }}
            valueKey="id"
            labelKey="name"
            hasAll={false}
            style={{width: '100%'}}
          />
        </Form.Item>
        <Form.Item 
          label={t('space.rerankModel')} 
          name="rerank"
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <CustomSelect
            url={getModelListUrl}
            params={{ type: 'rerank', pagesize: 100 }}
            valueKey="id"
            labelKey="name"
            hasAll={false}
            style={{width: '100%'}}
          />
        </Form.Item>
        
        <FormItem
          name="storage_type"
          label={t('space.storageType')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <RadioGroupCard
            options={types.map((type) => ({
              value: type,
              label: t(`space.${type}`),
              labelDesc: t(`space.${type}Desc`),
              // icon: typeIcons[type]
            }))}
          />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default SpaceModal;