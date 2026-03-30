/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:48:03 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-03-30 11:36:24 
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
import ModelSelect from '@/components/ModelSelect';

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
    <div className="rb:bg-white rb:rounded-lg rb:p-6 rb:pb-8">
      <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[#212332] rb:leading-5">{t('menu.spaceConfig')}</div>
      <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4 rb:mt-2 rb:mb-6">{t('space.configAlert')}</div>
      {pageLoading
        ? <Skeleton active />
        : <Form
          form={form}
          layout="vertical"
        >
          <Form.Item 
            label={t('space.llmModel')}
            className="rb:font-medium rb:text-[#212332] rb:mb-6!"
            name="llm"
            rules={[{ required: true, message: t('common.pleaseSelect') }]}
          >
            <ModelSelect
              params={{ type: 'llm' }}
              className="rb:w-137.5!"
            />
          </Form.Item>
          <Form.Item 
            label={t('space.embeddingModel')}
            className="rb:font-medium rb:text-[#212332] rb:mb-6!"
            name="embedding"
            rules={[{ required: true, message: t('common.pleaseSelect') }]}
          >
            <ModelSelect
              params={{ type: 'embedding' }}
              className="rb:w-137.5!"
            />
          </Form.Item>
          <Form.Item 
            label={t('space.rerankModel')}
            className="rb:font-medium rb:text-[#212332] rb:mb-6!"
            name="rerank"
            rules={[{ required: true, message: t('common.pleaseSelect') }]}
          >
            <ModelSelect
              params={{ type: 'rerank' }}
              className="rb:w-137.5!"
            />
          </Form.Item>

          <Button type="primary" className="rb:mt-1" onClick={handleSave} loading={loading}>
            {t('common.save')}
          </Button>
        </Form>
      }
    </div>
  );
};

export default SpaceConfig;