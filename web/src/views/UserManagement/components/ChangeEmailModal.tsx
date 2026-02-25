/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-25 11:45:07 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-25 11:45:07 
 */
/**
 * ChangeEmailModal Component
 * 
 * A two-step modal for changing user email address with verification code.
 * Step 1: Enter new email and send verification code
 * Step 2: Confirm the email change
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App, Row, Col, Button, Steps } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ChangeEmailModalRef, ChangeEmailModalForm } from '../types'
import RbModal from '@/components/RbModal'
import { changeEmail, sendEmailCode } from '@/api/user'
import { useUser } from '@/store/user';
import RbAlert from '@/components/RbAlert';
import Empty from '@/components/Empty';
import EmailIcon from '@/assets/images/login/email.svg'

const FormItem = Form.Item;

/**
 * Component props interface
 */
interface ChangeEmailModalProps {
  /** Callback function to refresh user data after email change */
  refresh: () => void;
}

const steps = [
  'bindNewEmail',
  'sureChange',
]

const ChangeEmailModal = forwardRef<ChangeEmailModalRef, ChangeEmailModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<ChangeEmailModalForm>();
  const [loading, setLoading] = useState(false)
  const [current, setCurrent] = useState<number>(0);
  const { user } = useUser();
  const [codeLoading, setCodeLoading] = useState(false)
  const [countdown, setCountdown] = useState(0)
  const newEmail = Form.useWatch(['new_email'], form)

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setCurrent(0)
    setCountdown(0)
  };
  /** Handle cancel button click - go back to previous step or close modal */
  const handleCancel = () => {
    if (current === 0) {
      handleClose()
    } else {
      setCurrent(0)
    }
  }

  /** Open modal */
  const handleOpen = () => {
    form.resetFields();
    setVisible(true);
  };
  /** Handle save/next button click - proceed to next step or submit email change */
  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        if (current === 0) {
          setCurrent(1)
        } else {
          setLoading(true)
          changeEmail(values)
            .then(() => {
              setLoading(false)
              refresh()
              handleClose()
              message.success(t('user.changeSuccess'))
            })
            .catch(() => {
              setLoading(false)
            });
        }
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  /** Send verification code to new email with countdown timer */
  const handleSendCode = () => {
    if (countdown > 0) {
      message.warning(t('user.sendCodeTooFrequent', { seconds: countdown }));
      return;
    }
    form
      .validateFields(['new_email'])
      .then((values) => {
        setCodeLoading(true)
        sendEmailCode({ email: values.new_email })
          .then(() => {
            message.success(t('user.sendSuccess'))
            setCountdown(300)
            const timer = setInterval(() => {
              setCountdown((prev) => {
                if (prev <= 1) {
                  clearInterval(timer)
                  return 0
                }
                return prev - 1
              })
            }, 1000)
          })
          .finally(() => {
            setCodeLoading(false)
          })
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t(`user.${steps[current]}`)}
      open={visible}
      onCancel={handleClose}
      footer={[
        <Button key="cancel" onClick={handleCancel}>{current === 1 ? t('common.prevStep') : t('common.cancel')}</Button>,
        <Button key="ok" loading={loading} type="primary" onClick={handleSave}>{current === 0 ? t('common.nextStep') : t('user.sureChange')}</Button>,
      ]}
    >
      <div className='rb:p-3 rb:bg-[#FBFDFF] rb:rounded-lg rb:border rb:border-[#DFE4ED] rb:mb-3'>
        <Steps
          labelPlacement="vertical"
          size="small"
          current={current}
          items={steps.map(key => ({ title: t(`user.${key}`) }))}
        />
      </div>
      {current === 0 && <RbAlert className="rb:mb-4!">{t('user.currentEmail')}: {user.email}</RbAlert>}
      {current === 1 && <Empty url={EmailIcon} size={80} isNeedSubTitle={false}
        title={<div className="rb:text-center">
          {t('user.sureChangeEmail')}<br />
          <div className="rb:font-medium rb:text-[#155EEF] rb:text-[16px]">{newEmail}</div>
          {t('user.sureChangeEmailDesc')}
        </div>} />}
      <Form
        form={form}
        layout="vertical"
        hidden={current === 1}
      >
        <Row gutter={16} className="rb:mb-6!">
          <Col span={16}>
            <Form.Item
              name="new_email"
              label={t('user.newEmail')}
              rules={[
                { required: true, message: t('common.pleaseEnter') },
                { type: 'email', message: t('user.emailFormatError') },
                {
                  validator: (_, value) => {
                    if (value && value === user.email) {
                      return Promise.reject(new Error(t('user.newEmailSameAsOld')));
                    }
                    return Promise.resolve();
                  }
                }
              ]}
              className="rb:mb-0!"
            >
              <Input placeholder={t('common.enter')} />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Button
              className="rb:mt-7.5"
              disabled={countdown > 0}
              loading={codeLoading}
              onClick={handleSendCode}
            >{countdown > 0 ? t('user.retrySend', { seconds: countdown }) : t('user.sendEmailCode')}</Button>
          </Col>
        </Row>
        <FormItem
          name="code"
          label={t('user.emailCode')}
          rules={[
            { required: true, message: t('common.pleaseEnter') },
            { len: 6, message: t('user.emailCodeLengthRule') }
          ]}
        >
          <Input placeholder={t('user.emailCodePlaceholder')} />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default ChangeEmailModal;