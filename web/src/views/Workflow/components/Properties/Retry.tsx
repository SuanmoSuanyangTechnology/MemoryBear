/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-21 18:51:34 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-05-21 18:51:34 
 */
/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-09 18:35:43 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-21 15:22:45
 */
import { type FC } from "react";
import { useTranslation } from 'react-i18next'
import { Form, Divider, Switch } from 'antd'

import RbSlider from "@/components/RbSlider";

const Retry: FC = () => {
  const { t } = useTranslation()
  const form = Form.useFormInstance();
  const values = Form.useWatch([], form) || {}

  return (
    <>
      <Divider />
      <Form.Item name={['retry', 'enable']} valuePropName="checked" layout="horizontal" label={t('workflow.config.http-request.retry')}>
        <Switch />
      </Form.Item>
      {(values?.retry?.enable || typeof values?.retry?.max_attempts === 'number' || typeof values?.retry?.retry_interval === 'number') &&
        <>
          <Form.Item
            name={['retry', 'max_attempts']}
            label={<span className="rb:text-[#5B6167]">{t('workflow.config.http-request.max_attempts')}</span>}
            className="rb:mb-2!"
          >
            <RbSlider
              min={1}
              max={10}
              step={1}
              isInput={true}
              size="small"
              inputClassName="rb:w-30!"
              suffix={t('workflow.config.times')}
            />
          </Form.Item>
          <Form.Item
            name={['retry', 'retry_interval']}
            label={<span className="rb:text-[#5B6167]">{t('workflow.config.http-request.retry_interval')}(ms)</span>}
            className="rb:mb-2!"
          >
            <RbSlider
              min={10}
              max={60000}
              step={1}
              isInput={true}
              size="small"
              inputClassName="rb:w-30!"
              suffix={t('workflow.config.ms')}
            />
          </Form.Item>
        </>
      }
    </>
  );
};
export default Retry;
