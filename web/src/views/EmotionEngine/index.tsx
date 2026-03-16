/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:56:54 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-12 16:58:14
 */
/**
 * Emotion Engine Configuration Page
 * Configures emotion analysis settings for memory system
 * Includes model selection, intensity threshold, and feature toggles
 */

import React, { useState, useEffect } from 'react';
import { Row, Col, Form, Button, message, Space, Flex, Tooltip } from 'antd';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import RbCard from '@/components/RbCard/Card';
import { getMemoryEmotionConfig, updateMemoryEmotionConfig } from '@/api/memory'
import type { ConfigForm } from './types'
import CustomSelect from '@/components/CustomSelect';
import { getModelListUrl } from '@/api/models'
import SwitchFormItem from '@/components/FormItem/SwitchFormItem'
import LabelWrapper from '@/components/FormItem/LabelWrapper'
import DescWrapper from '@/components/FormItem/DescWrapper'
import RbSlider from '@/components/RbSlider';
import RbAlert from '@/components/RbAlert';

/**
 * Configuration field definitions
 */
const configList = [
  {
    key: 'emotion_enabled',
    type: 'switch',
  },
  {
    key: 'emotion_model_id',
    type: 'customSelect',
    url: getModelListUrl,
    params: { type: 'chat,llm', page: 1, pagesize: 100, is_active: true }, // chat,llm
  },
  {
    key: 'emotion_min_intensity',
    type: 'decimal',
    min: 0,
    max: 1,
    step: 0.05,
    range: [0, 1],
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

/**
 * Emotion engine configuration component
 */
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

  /** Fetch emotion engine configuration */
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
  /** Reset form to saved configuration */
  const handleReset = () => {
    form.setFieldsValue(configData);
  }
  /** Save emotion engine configuration */
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
          title={t('emotionEngine.emotionEngineConfig')}
          headerType="borderless"
          headerClassName="rb:min-h-[54px]! rb:font-[MiSans-Bold] rb:font-bold"
          extra={<Space>
            <Button block onClick={handleReset}>{t('common.reset')}</Button>
            <Button type="primary" loading={loading} block onClick={handleSave}>{t('common.save')}</Button>
          </Space>}
          className="rb:h-[calc(100vh-76px)]!"
          bodyClassName="rb:h-[calc(100%-54px)] rb:overflow-y-auto! rb:p-3! rb:pt-0!"
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
            <Flex vertical gap={12}>
              {configList.map(config => {
                if (config.type === 'decimal') {
                  return (
                    <div key={config.key} className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-3">
                      <Flex align="center" gap={4} className="rb:text-[14px] rb:font-medium rb:leading-5 rb:mb-2">
                        {t(`emotionEngine.${config.key}`)}
                        <Tooltip title={t(`emotionEngine.${config.key}_desc`)}>
                          <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')]"></div>
                        </Tooltip>
                      </Flex>
                      <Form.Item
                        name={config.key}
                        extra={<DescWrapper
                          desc={<>
                            <span className="rb:text-[12px]">{t(`forgettingEngine.range`)}: {config.range?.join('-')}</span> | <span>{t(`forgettingEngine.type`)}: {config.type}</span>
                          </>}
                        />}
                        className="rb:mb-0!"
                      >
                        <RbSlider
                          max={config.max}
                          min={config.min}
                          step={config.step}
                          isInput={true}
                          prefix={<span className="rb:text-[#5B6167]">{t('emotionEngine.currentValue')}:</span>}
                          inputClassName="rb:w-[155px]!"
                        />
                      </Form.Item>
                    </div>
                  )
                }
                if (config.type === 'customSelect') {
                  return (
                    <div key={config.key} className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-3">
                      <LabelWrapper title={t(`emotionEngine.${config.key}`)} className="rb:mb-3">
                        <DescWrapper desc={t(`emotionEngine.${config.key}_desc`)} className="rb:mt-1" />
                      </LabelWrapper>
                      <Form.Item
                        name={config.key}
                        className="rb:mb-0!"
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
                  <SwitchFormItem
                    title={t(`emotionEngine.${config.key}`)}
                    name={config.key}
                    desc={<>
                      {config.hasSubTitle && <div className="rb:mt-1 rb:text-[#5B6167] rb:font-regular rb:leading-4">{t(`emotionEngine.${config.key}_subTitle`)}</div>}
                      <div className="rb:mt-1  rb:text-[#5B6167] rb:font-regular rb:leading-4">{t(`emotionEngine.${config.key}_desc`)}</div>
                    </>}
                    disabled={!values?.emotion_enabled && config.key !== 'emotion_enabled'}
                    className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-3!"
                  />
                )
              })}
            </Flex>
          </Form>
        </RbCard>
      </Col>
      <Col span={12}>
        <RbCard
          title={t('emotionEngine.emotionEngineConfig')}
          headerType="borderless"
          headerClassName="rb:min-h-[54px]! rb:font-[MiSans-Bold] rb:font-bold"
          className="rb:h-[calc(100vh-76px)]!"
          bodyClassName="rb:h-[calc(100%-54px)] rb:overflow-y-auto! rb:p-3! rb:pt-0!"
        >
          <Flex vertical gap={24} className="rb:text-[#212332]">
            <div>
              <div className="rb:font-medium rb:leading-5 rb:px-1 rb:mb-2.5">{t('emotionEngine.question')}</div>
              <div className="rb:text-[#5B6167] rb:bg-[#F6F6F6] rb:px-3 rb:py-2.5 rb:font-regular rb:leading-5 rb:rounded-xl">
                {t('emotionEngine.answer')}
              </div>
            </div>

            <div>
              <div className="rb:font-medium rb:leading-5 rb:px-1 rb:mb-2.5">{t('emotionEngine.differentTitle')}</div>

              <Flex gap={10} vertical>
                {['low', 'middle', 'high'].map((key, index) => (
                  <RbAlert
                    key={key}
                    color={(['orange', 'blue', 'green'] as const)[index] as 'orange' | 'blue' | 'green'}
                  >
                    <Flex gap={10} vertical className=" rb:text-[#5B6167] rb:text-[14px] rb:font-regular rb:leading-5">
                      <Flex align="center" gap={8}>
                        <span className="rb:font-medium rb:text-[#212332]">{t(`emotionEngine.${key}_title`)}</span>

                        <span className={clsx("rb:px-1 rb:rounded-sm rb:text-white rb:leading-4.5", ['rb:bg-[#FF5D34]', 'rb:bg-[#155EEF]', 'rb:bg-[#369F21]'][index])}>
                          {t(`emotionEngine.${key}_tag`)}
                        </span>
                      </Flex>
                      <div><span className="rb:font-medium rb:text-[#212332]">{t('emotionEngine.advantage')}: </span>{t(`emotionEngine.${key}_advantage`)}</div>
                      <div><span className="rb:font-medium rb:text-[#212332]">{t('emotionEngine.shortcoming')}: </span>{t(`emotionEngine.${key}_shortcoming`)}</div>
                      <div><span className="rb:font-medium rb:text-[#212332]">{t('emotionEngine.scene')}: </span>{t(`emotionEngine.${key}_scene`)}</div>
                    </Flex>
                  </RbAlert>
                ))}
              </Flex>
            </div>

            <div>
              <div className="rb:font-medium rb:leading-5 rb:px-1 rb:mb-2.5">{t('emotionEngine.configSuggest')}</div>
              <Flex gap={10} vertical>
                {['first', 'customer_service', 'data_analysis', 'risk_warning'].map(key => (
                  <div className="rb:bg-[#F6F6F6] rb:px-3 rb:py-2.5 rb:rounded-xl">{t(`emotionEngine.${key}`)}: {t(`emotionEngine.${key}_desc`)}</div>
                ))}
              </Flex>
            </div>

            <div>
              <div className="rb:font-medium rb:leading-5 rb:px-1 rb:mb-2.5">{t('emotionEngine.actual_case')}</div>

              <div className="rb:bg-[#F6F6F6] rb:px-3 rb:py-2.5 rb:font-regular rb:leading-5 rb:rounded-xl">
                <div className="rb:mb-2.5">
                  <span className="rb:font-medium">{t('emotionEngine.user_input')}: </span>
                  {t('emotionEngine.user_input_message')}
                </div>

                <Flex vertical gap={4}>
                  {['neutral_emotion', 'minor_dissatisfaction', 'expect_improvement'].map((key, index) => (
                    <Flex gap={28} align="center" justify="space-between" className="rb:bg-white rb:px-3! rb:py-2! rb:rounded-lg">
                      <Flex align="center" justify="space-between" className="rb:w-[55%]!">
                        <span className="rb:font-medium">{t(`emotionEngine.${key}`)}</span>
                        <span>{t('emotionEngine.confidence')}: {key === 'neutral_emotion' ? 0.85 : key === 'minor_dissatisfaction' ? 0.45 : 0.32}</span>
                      </Flex>

                      <span className={clsx('rb:text-right rb:wrap-break-word rb:flex-1', ['rb:text-[#369F21]', 'rb:text-[#FF5D34]', 'rb:text-[#155EEF]'][index])}>{t(`emotionEngine.${key}_tag`)}</span>
                    </Flex>
                  ))}
                </Flex>
              </div>
            </div>
          </Flex>
        </RbCard>
      </Col>
      {contextHolder}
    </Row>
  );
};

export default EmotionEngine;
