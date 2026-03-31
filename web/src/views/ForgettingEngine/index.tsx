/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:00:12 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-26 15:47:37
 */
/**
 * Forgetting Engine Configuration Page
 * Configures memory forgetting curve parameters
 * Uses Ebbinghaus forgetting curve formula: R = offset + (1 - offset) × e^(-λ_time × t / λ_mem)
 */

import React, { useState, useEffect } from 'react';
import { Row, Col, Form, Button, Space, App, Flex, Tooltip } from 'antd';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import RbCard from '@/components/RbCard/Card';
import LineChart from './components/LineChart'
import { getMemoryForgetConfig, updateMemoryForgetConfig } from '@/api/memory'
import type { ConfigForm } from './types'
import SwitchFormItem from '@/components/FormItem/SwitchFormItem'
import RbSlider from '@/components/RbSlider';
import DescWrapper from '@/components/FormItem/DescWrapper'

/**
 * Configuration field definitions
 */
const configList = [
  {
    key: 'minimumRetention',
    name: 'lambda_time',
    range: [0, 1],
    type: 'decimal',
  },
  {
    key: 'forgettingRate',
    name: 'lambda_mem',
    range: [0.01, 1],
    type: 'decimal',
  },
  {
    key: 'offset',
    name: 'offset',
    range: [0, 1],
    type: 'decimal',
  },
  {
    key: 'decay_constant',
    name: 'decay_constant',
    range: [0, 1],
    type: 'decimal',
    hiddenDesc: true,
  },
  {
    key: 'max_history_length',
    name: 'max_history_length',
    type: 'decimal',
    step: 1,
    range: [10, 1000],
    hiddenDesc: true,
  },
  {
    key: 'forgetting_threshold',
    name: 'forgetting_threshold',
    type: 'decimal',
    range: [0, 1],
    hiddenDesc: true,
  },
  {
    key: 'min_days_since_access',
    name: 'min_days_since_access',
    type: 'decimal',
    step: 1,
    range: [1, 365],
    hiddenDesc: true,
  },
  {
    key: 'enable_llm_summary',
    name: 'enable_llm_summary',
    type: 'button',
    hiddenDesc: true,
  },
  {
    key: 'max_merge_batch_size',
    name: 'max_merge_batch_size',
    type: 'decimal',
    step: 1,
    range: [1, 1000],
    hiddenDesc: true,
  },
  {
    key: 'forgetting_interval_hours',
    name: 'forgetting_interval_hours',
    type: 'decimal',
    step: 1,
    range: [1, 168],
    hiddenDesc: true,
  },
]

/**
 * Forgetting engine configuration component
 */
const ForgettingEngine: React.FC = () => {
  const { t } = useTranslation();
  const { id } = useParams();
  const [configData, setConfigData] = useState<ConfigForm>();
  const [form] = Form.useForm<ConfigForm>();
  const { message: messageApi } = App.useApp();
  const [loading, setLoading] = useState(false)

  const values = Form.useWatch([], form);

  useEffect(() => {
    getConfigData()
  }, [])

  /** Fetch forgetting engine configuration */
  const getConfigData = () => {
    getMemoryForgetConfig(id as string)
      .then((res) => {
        const response = res as ConfigForm
        const initialValues = {
          ...response,
          lambda_time: Number(response.lambda_time || 0),
          lambda_mem: Number(response.lambda_mem || 0),
          offset: Number(response.offset || 0),
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
    form.setFieldsValue(configData || {});
  }
  /** Save forgetting engine configuration */
  const handleSave = () => {
    setLoading(true)
    updateMemoryForgetConfig({
      config_id: id,
      ...values
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
    <Row gutter={12} className="rb:h-full!">
      <Col span={12} className="rb:h-full!">
        <RbCard
          title={t('forgettingEngine.forgettingEngineConfigParams')}
          extra={<Space>
            <Button block onClick={handleReset}>{t('common.reset')}</Button>
            <Button type="primary" loading={loading} block onClick={handleSave}>{t('common.save')}</Button>
          </Space>}
          headerType="borderless"
          headerClassName="rb:min-h-[54px]! rb:font-[MiSans-Bold] rb:font-bold"
          className="rb:h-full!"
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
                if (config.type === 'button') {
                  return (
                    <SwitchFormItem
                      title={t(`forgettingEngine.${config.key}`)}
                      name={config.name}
                      desc={config.type && <span>{t(`forgettingEngine.type`)}: {config.type}</span>}
                      className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-3!"
                    />
                  )
                }
                return (
                  <div key={config.key} className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-3">
                    <Flex align="center" gap={4} className="rb:text-[14px] rb:font-medium rb:leading-5 rb:mb-2">
                      {t(`forgettingEngine.${config.key}`)}
                      {!config.hiddenDesc && <Tooltip title={t(`forgettingEngine.${config.key}Desc`)}>
                        <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')]"></div>
                      </Tooltip>}
                    </Flex>
                    
                    <Form.Item
                      name={config.name}
                      extra={<DescWrapper
                        desc={<>
                          <span className="rb:text-[12px]">{t(`forgettingEngine.range`)}: {config.range?.join('-')}</span> | <span>{t(`forgettingEngine.type`)}: {config.type}</span>
                        </>}
                      />}
                      className="rb:mb-0!"
                    >
                      {config.type === 'decimal'
                        ? <RbSlider
                          max={config.range?.[1] || 1}
                          min={config.range?.[0] || 0}
                          step={config.step ?? 0.01}
                          isInput={true}
                          prefix={<span className="rb:text-[#5B6167]">{t('emotionEngine.currentValue')}:</span>}
                          inputClassName="rb:w-[155px]!"
                        />
                        : null
                      }
                    </Form.Item>
                  </div>
                )
              })}
            </Flex>
          </Form>
        </RbCard>
      </Col>
      <Col span={12} className="rb:h-full!">
        <RbCard
          title={t('forgettingEngine.forgettingCurve')}
          headerType="borderless"
          headerClassName="rb:min-h-[54px]! rb:font-[MiSans-Bold] rb:font-bold"
          bodyClassName="rb:p-3! rb:pt-0!"
        >
          <LineChart
            config={values}
          />
        </RbCard>
      </Col>
    </Row>
  );
};

export default ForgettingEngine;
