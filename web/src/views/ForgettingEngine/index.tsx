import React, { useState, useEffect } from 'react';
import { Row, Col, Form, Slider, Button, Space, message } from 'antd';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import RbCard from '@/components/RbCard/Card';
import strategyImpactSimulator from '@/assets/images/memory/strategyImpactSimulator.svg'
import LineChart from './components/LineChart'
import { getMemoryForgetConfig, updateMemoryForgetConfig } from '@/api/memory'
import type { ConfigForm } from './types'

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
    range: [0, 1],
    type: 'decimal',
  },
  {
    key: 'offset',
    name: 'offset',
    type: 'decimal',
  }
]

const ForgettingEngine: React.FC = () => {
  const { t } = useTranslation();
  const params = useParams();
  const [configData, setConfigData] = useState<ConfigForm>();
  const [form] = Form.useForm<ConfigForm>();
  const [messageApi, contextHolder] = message.useMessage();
  const [loading, setLoading] = useState(false)

  const values = Form.useWatch([], form);

  useEffect(() => {
    getConfigData()
  }, [])

  const getConfigData = () => {
    getMemoryForgetConfig(params.id)
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
    form.setFieldsValue(configData);
  }
  const handleSave = () => {
    setLoading(true)
    updateMemoryForgetConfig({
      config_id: params.id,
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
              <img src={strategyImpactSimulator} className="rb:w-[20px] rb:h-[20px] rb:mr-[8px]" />
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
              {configList.map(config => (
                <div key={config.key}>
                  <div className="rb:text-[14px] rb:font-medium rb:leading-[20px] rb:mb-[8px]">
                    {t(`forgettingEngine.${config.key}`)}
                  </div>
                  <div className="rb:mt-[4px] rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-[16px] ">
                    {t(`forgettingEngine.${config.key}Desc`)}
                  </div>
                  <Form.Item
                    name={config.name}
                  >
                    <Slider tooltip={{open: false}} max={config.range?.[1] || 1} min={config.range?.[0] || 0} step={0.01} style={{ margin: '0' }} />
                  </Form.Item>
                  <div className="rb:flex rb:text-[12px] rb:items-center rb:justify-between rb:text-[#5B6167] rb:leading-[20px] rb:mt-[-26px]">
                    <Space size={4}>
                      {config.range && <span>{t(`forgettingEngine.range`)}: {config.range?.join('-')}</span>}
                      {config.type && <span>{t(`forgettingEngine.type`)}: {config.type}</span>}
                    </Space>
                    <>{t('forgettingEngine.CurrentValue')}: {values?.[config.name] || 0}</>
                  </div>
                </div>
              ))}
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
          className='rb:h-full!'
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
