/*
 * @Author: zhaoying
 * @Date: 2026-06-02 20:25:20
 * @Last Modified by: zhaoying
 * @Last Modified time: 
 */
import { type FC } from 'react'
import { Form, Select, Flex, Switch } from 'antd';
import { useTranslation } from 'react-i18next';

import Schedule from './Schedule'
import Webhook from './Webhook'
import { randomString } from '@/utils/common'

const TRIGGER_TYPE_OPTIONS = ['schedule', 'webhook']
const Trigger: FC = () => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();
  const values = Form.useWatch([], form);

  const handleChange = (type: string) => {
    if (type === 'webhook') {
      form.setFieldValue('route_key', randomString(16, false))
    }
  }

  return (
    <>
      <Flex gap={8} align="center" justify="space-between" className="rb:mb-4!">
        <Form.Item
          name="enabled"
          layout="horizontal"
          label={t('workflow.trigger')}
          className="rb:mb-0!"
        >
          <Switch />
        </Form.Item>
        <Form.Item
          name="trigger_type"
          className="rb:mb-0! rb:flex-1!"
        >
          <Select
            options={TRIGGER_TYPE_OPTIONS.map(type => ({
              value: type,
              label: t(`workflow.config.trigger.${type}`),
            }))}
            size="small"
            placeholder={t('common.pleaseSelect')}
            onChange={handleChange}
          />
        </Form.Item>
      </Flex>

      {values?.trigger_type === 'schedule' && <Schedule />}
      {values?.trigger_type === 'webhook' && <Webhook />}
    </>
  );
};

export default Trigger;
