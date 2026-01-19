import React, { useState, useEffect } from 'react';
import { Row, Col, Form, Slider, Button, Space, message } from 'antd';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import RbCard from '@/components/RbCard/Card';
import strategyImpactSimulator from '@/assets/images/memory/strategyImpactSimulator.svg'
import LineChart from './components/LineChart'
import { getMemoryForgetConfig, updateMemoryForgetConfig } from '@/api/memory'
import type { ConfigForm } from './types'
import SwitchFormItem from '@/components/FormItem/SwitchFormItem'

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

const ForgettingEngine: React.FC = () => {
  const { t } = useTranslation();
  const { id } = useParams();
  const [configData, setConfigData] = useState<ConfigForm>();
  const [form] = Form.useForm<ConfigForm>();
  const [messageApi, contextHolder] = message.useMessage();
  const [loading, setLoading] = useState(false)

  const values = Form.useWatch([], form);

  useEffect(() => {
    getConfigData()
  }, [])

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
  const handleReset = () => {
    form.setFieldsValue(configData || {});
  }
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
    <Row gutter={[16, 16]}>
      <Col span={9}>
        <RbCard 
          title={
            <div className="rb:flex rb:items-center">
              <img src={strategyImpactSimulator} className="rb:w-5 rb:h-5 rb:mr-2" />
              {t('forgettingEngine.forgettingEngineConfigParams')}
            </div>
          }
          className='rb:h-full!'
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
            <Space size={24} direction="vertical" style={{ width: '100%' }}>
              {configList.map(config => {
                if (config.type === 'button') {
                  return (
                    <SwitchFormItem
                      title={t(`forgettingEngine.${config.key}`)}
                      name={config.name}
                      desc={config.type && <span>{t(`forgettingEngine.type`)}: {config.type}</span>}
                      className="rb:mb-2"
                    />
                  )
                }
                return (
                  <div key={config.key}>
                    <div className="rb:text-[14px] rb:font-medium rb:leading-5 rb:mb-2">
                      {t(`forgettingEngine.${config.key}`)}
                    </div>
                    {!config.hiddenDesc && <div className="rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4 ">
                      {t(`forgettingEngine.${config.key}Desc`)}
                    </div>}
                    
                    <Form.Item
                      name={config.name}
                    >
                      {config.type === 'decimal'
                        ? <Slider tooltip={{ open: false }} max={config.range?.[1] || 1} min={config.range?.[0] || 0} step={config.step ?? 0.01} style={{ margin: '0' }} />
                        : null
                      }
                    </Form.Item>
                    <div className="rb:flex rb:text-[12px] rb:items-center rb:justify-between rb:text-[#5B6167] rb:leading-5 rb:-mt-6.5">
                      <Space size={4}>
                        {config.range && <span>{t(`forgettingEngine.range`)}: {config.range?.join('-')}</span>}
                        {config.type && <span>{t(`forgettingEngine.type`)}: {config.type}</span>}
                      </Space>
                      <>{t('forgettingEngine.CurrentValue')}: {values?.[config.name] || 0}</>
                    </div>
                  </div>
                )
              })}
              <Row gutter={16}>
                <Col span={12}>
                  <Button block onClick={handleReset}>{t('common.reset')}</Button>
                </Col>
                <Col span={12}>
                  <Button type="primary" loading={loading} block onClick={handleSave}>{t('common.save')}</Button>
                </Col>
              </Row>
            </Space>
          </Form>
        </RbCard>
      </Col>
      <Col span={15}>
        <RbCard
        >
          <LineChart
            config={values}
          />
        </RbCard>
      </Col>
      {contextHolder}
    </Row>
  );
};

export default ForgettingEngine;
