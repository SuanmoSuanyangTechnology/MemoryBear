/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:48:03 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:48:03 
 */
/**
 * Space Configuration Page
 * Configures default models for workspace (LLM, embedding, rerank)
 */

import { type FC, useEffect, useState } from 'react';
import { Form, App, Button, Skeleton } from 'antd';
import { useTranslation } from 'react-i18next';

import type { SpaceConfigData } from './types'
import { getWorkspaceModels, updateWorkspaceModels } from '@/api/workspaces'
import { getModelListUrl } from '@/api/models'
import CustomSelect from '@/components/CustomSelect'
import RbAlert from '@/components/RbAlert';

const SpaceConfig: FC = () => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [pageLoading, setPageLoding] = useState(false)
  const [form] = Form.useForm<SpaceConfigData>();
  const [loading, setLoading] = useState(false)

  const values = Form.useWatch([], form);

  useEffect(() => {
    setPageLoding(true)
    getWorkspaceModels().then((res) => {
      const { llm, embedding, rerank } = res as SpaceConfigData
      form.setFieldsValue({
        llm,
        embedding,
        rerank
      })
    })
    .finally(() => {
      setPageLoding(false)
    })
  }, [])
  /** Save configuration */
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        setLoading(true)
        updateWorkspaceModels(values)
          .then(() => {
            setLoading(false)
            message.success(t('common.updateSuccess'))
          })
          .catch(() => {
            setLoading(false)
          });
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  return (
    <div className="rb:h-full rb:max-w-140 rb:mx-auto">
      {pageLoading
        ? <Skeleton active />
        : <Form
          form={form}
          layout="vertical"
        >
          <Form.Item 
            label={t('space.llmModel')} 
            name="llm"
            rules={[{ required: true, message: t('common.pleaseSelect') }]}
          >
            <CustomSelect
              url={getModelListUrl}
              params={{ type: 'llm', pagesize: 100, is_active: true }}
              valueKey="id"
              labelKey="name"
              hasAll={false}
              style={{width: '100%'}}
            />
          </Form.Item>
          <Form.Item 
            label={t('space.embeddingModel')} 
            name="embedding"
            rules={[{ required: true, message: t('common.pleaseSelect') }]}
          >
            <CustomSelect
              url={getModelListUrl}
              params={{ type: 'embedding', pagesize: 100, is_active: true }}
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
              params={{ type: 'rerank', pagesize: 100, is_active: true }}
              valueKey="id"
              labelKey="name"
              hasAll={false}
              style={{width: '100%'}}
            />
          </Form.Item>

          <RbAlert>{t('space.configAlert')}</RbAlert>

          <Form.Item className="rb:text-right">
            <Button type="primary" className="rb:mt-6" onClick={handleSave} loading={loading}>
              {t('common.save')}
            </Button>
          </Form.Item>
        </Form>
      }
    </div>
  );
};

export default SpaceConfig;