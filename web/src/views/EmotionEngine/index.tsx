import React, { useState, useEffect } from 'react';
import { Row, Col, Form, Slider, Button, Alert, message, Switch, Space } from 'antd';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import RbCard from '@/components/RbCard/Card';
import strategyImpactSimulator from '@/assets/images/memory/strategyImpactSimulator.svg'
import { getMemoryEmotionConfig, updateMemoryEmotionConfig } from '@/api/memory'
import type { ConfigForm } from './types'
import CustomSelect from '@/components/CustomSelect';
import { getModelListUrl } from '@/api/models'
import Tag from '@/components/Tag'

const configList = [
  {
    key: 'emotion_enabled',
    type: 'switch',
  },
  {
    key: 'emotion_model_id',
    type: 'customSelect',
    url: getModelListUrl,
    params: { type: 'chat,llm', page: 1, pagesize: 100 }, // chat,llm
  },
  {
    key: 'emotion_min_intensity',
    type: 'slider',
    min: 0,
    max: 1,
    step: 0.05
  },
  {
    key: 'emotion_extract_keywords',
    type: 'switch',
    hasSubTitle: true
  },
  {
    key: 'emotion_enable_subject',
    type: 'switch',
    hasSubTitle: true
  },
]

const EmotionEngine: React.FC = () => {
  const { t } = useTranslation();
  const { id } = useParams();
  const [configData, setConfigData] = useState<ConfigForm>({} as ConfigForm);
  const [form] = Form.useForm<ConfigForm>();
  const [messageApi, contextHolder] = message.useMessage();
  const [loading, setLoading] = useState(false)

  const values = Form.useWatch([], form);

  useEffect(() => {
    getConfigData()
  }, [id])

  const getConfigData = () => {
    if (!id) {
      return
    }
    getMemoryEmotionConfig(id)
      .then((res) => {
        const response = res as ConfigForm
        const initialValues = {
          ...response,
        }
        setConfigData(initialValues);
        form.setFieldsValue(initialValues);
      })
      .catch(() => {
        console.error('Failed to load data');
      })
  }
  const handleReset = () => {
    form.setFieldsValue(configData);
  }
  const handleSave = () => {
    if (!id) {
      return
    }
    setLoading(true)
    updateMemoryEmotionConfig({
      ...values,
      config_id: id
    })
      .then(() => {
        messageApi.success(t('common.saveSuccess'))
        setConfigData({...(values || {})})
      })
      .finally(() => {
        setLoading(false)
      })
  }

  return (
    <Row gutter={[16, 16]}>
      <Col span={12}>
        <RbCard 
          title={
            <div className="rb:flex rb:items-center">
              <img src={strategyImpactSimulator} className="rb:w-5 rb:h-5 rb:mr-2" />
              {t('emotionEngine.emotionEngineConfig')}
            </div>
          }
        >
          <Form 
            form={form}
            layout="vertical"
            initialValues={{
              offset: 0,
              lambda_time: 0.03,
              lambda_mem: 0.03,
            }}
          >
            {configList.map(config => {
              if (config.type === 'slider') {
                return (
                  <div key={config.key} className=" rb:mb-6">
                    <div className="rb:text-[14px] rb:font-medium rb:leading-5 rb:mb-2">
                      {t(`emotionEngine.${config.key}`)}
                    </div>
                    <div className="rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4 ">
                      {t(`emotionEngine.${config.key}_desc`)}
                    </div>
                    <Form.Item
                      name={config.key}
                    >
                      <Slider
                        disabled={!values?.emotion_enabled && config.key !== 'emotion_enabled'}
                        tooltip={{ open: false }} max={config.max} min={config.min} step={config.step} style={{ margin: '0' }} />
                    </Form.Item>
                    <div className="rb:flex rb:text-[12px] rb:items-center rb:justify-between rb:text-[#5B6167] rb:leading-5 rb:-mt-6.5">

                      <>{t('emotionEngine.currentValue')}: {values?.[config.key as keyof ConfigForm] || 0}</>
                    </div>
                  </div>
                )
              }
              if (config.type === 'customSelect') {
                return (
                  <div key={config.key}>
                    <div className="rb:text-[14px] rb:font-medium rb:leading-5 rb:mb-2">
                      {t(`emotionEngine.${config.key}`)}
                    </div>
                    <Form.Item
                      name={config.key}
                      extra={t(`emotionEngine.${config.key}_desc`)}
                    >
                      <CustomSelect
                        url={config.url as string}
                        params={config.params}
                        valueKey='id'
                        labelKey='name'
                        hasAll={false}
                        disabled={!values?.emotion_enabled && config.key !== 'emotion_enabled'}
                      />
                    </Form.Item>
                  </div>
                )
              }

              return (
                <div className="rb:flex rb:items-center rb:justify-between rb:mb-6">
                  <div>
                    <span className="rb:text-[14px] rb:font-medium rb:leading-5">{t(`emotionEngine.${config.key}`)}</span>
                    {config.hasSubTitle && <div className="rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4">{t(`emotionEngine.${config.key}_subTitle`)}</div>}
                    <div className="rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4">{t(`emotionEngine.${config.key}_desc`)}</div>
                  </div>
                  <Form.Item
                    name={config.key}
                    valuePropName="checked"
                    className="rb:ml-2 rb:mb-0!"
                  >
                    <Switch
                      disabled={!values?.emotion_enabled && config.key !== 'emotion_enabled'} />
                  </Form.Item>
                </div>
              )
            })}
            <Row gutter={16} className="rb:mt-3">
              <Col span={12}>
                <Button block onClick={handleReset}>{t('common.reset')}</Button>
              </Col>
              <Col span={12}>
                <Button type="primary" loading={loading} block onClick={handleSave}>{t('common.save')}</Button>
              </Col>
            </Row>
          </Form>
        </RbCard>
      </Col>
      <Col span={12}>
        <RbCard
          title={t('emotionEngine.emotion_min_intensity_description')}
        >
          <div className="rb:font-medium">{t('emotionEngine.question')}</div>
          <div className="rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4 rb:mt-2">{t('emotionEngine.answer')}</div>
          <div className="rb:font-medium rb:mt-4 rb:mb-2">{t('emotionEngine.differentTitle')}</div>

          <Space size={16} direction="vertical" className="rb:w-full">
            {['low', 'middle', 'high'].map((key, index) => (
              <Alert
                key={key} 
                type={(['warning', 'info', 'success'] as const)[index] as 'warning' | 'info' | 'success'}
                message={
                  <div>
                    <div className="rb:w-full rb:font-medium rb:flex rb:justify-between">
                      {t(`emotionEngine.${key}_title`)}
                      <Tag color={(['warning', 'processing', 'success'] as const)[index] as 'warning' | 'processing' | 'success'}>{t(`emotionEngine.${key}_tag`)}</Tag>
                    </div>
                    <Space size={8} direction="vertical" className="rb:w-full rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4">
                      <div><span className="rb:font-medium">{t('emotionEngine.advantage')}: </span>{t(`emotionEngine.${key}_advantage`)}</div>
                      <div><span className="rb:font-medium">{t('emotionEngine.shortcoming')}: </span>{t(`emotionEngine.${key}_shortcoming`)}</div>
                      <div><span className="rb:font-medium">{t('emotionEngine.scene')}: </span>{t(`emotionEngine.${key}_scene`)}</div>
                    </Space>
                  </div>
                }
              />
            ))}
          </Space>

          <div className="rb:font-medium rb:mt-6 rb:mb-3">{t('emotionEngine.configSuggest')}</div>
          <Space size={12} direction="vertical" className="rb:w-full">
            {['first', 'customer_service', 'data_analysis', 'risk_warning'].map(key => (
              <div className="rb:bg-[#F0F3F8] rb:px-3 rb:py-2.5 rb:rounded-md rb:text-[12px]">{t(`emotionEngine.${key}`)}: {t(`emotionEngine.${key}_desc`)}</div>
            ))}
          </Space>

          <div className="rb:font-medium rb:mt-6 rb:mb-3">{t('emotionEngine.actual_case')}</div>
          <Space size={12} direction="vertical" className="rb:w-full rb:bg-[#F0F3F8] rb:px-3 rb:py-2.5 rb:rounded-md">
            <div className="rb:bg-white rb:px-3 rb:py-2.5 rb:rounded-md">
              <span className="rb:font-medium">{t('emotionEngine.user_input')}: </span>
              {t('emotionEngine.user_input_message')}
            </div>
            {['neutral_emotion', 'minor_dissatisfaction', 'expect_improvement'].map((key, index) => (
              <div className="rb:flex rb:items-center rb:justify-between rb:bg-white rb:px-3 rb:py-2.5 rb:rounded-md">
                <div className="rb:w-[50%] rb:flex rb:items-center rb:justify-between rb:text-[12px]">
                  {t(`emotionEngine.${key}`)}
                  <span>{t('emotionEngine.confidence')}: {key === 'neutral_emotion' ? 0.85 : key === 'minor_dissatisfaction' ? 0.45 : 0.32}</span>
                </div>
                
                <Tag color={(['success', 'warning', 'processing'] as const)[index] as 'warning' | 'processing' | 'success'}>{t(`emotionEngine.${key}_tag`)}</Tag>
              </div>
            ))}
            </Space>
        </RbCard>
      </Col>
      {contextHolder}
    </Row>
  );
};

export default EmotionEngine;
