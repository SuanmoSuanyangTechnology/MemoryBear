/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:37:12 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:37:12 
 */
/**
 * Invite Register Page
 * Handles user registration via workspace invitation link
 * Validates invite token and allows new users to set up their account
 */

import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { Button, Input, Form, Progress, App } from 'antd';
import { ExclamationCircleFilled } from '@ant-design/icons';
import type { FormProps } from 'antd';

import { useUser, type LoginInfo } from '@/store/user';
import { login } from '@/api/user'
import inviteBg from '@/assets/images/login/inviteBg.png'
import checkBg from '@/assets/images/login/checkBg.png'
import type { LoginForm, ValidateToken } from './types';
import { validateInviteToken } from '@/api/member'
import RbAlert from '@/components/RbAlert'
import styles from './index.module.css'

/**
 * Alert extra content wrapper
 */
const Extra = ({ children }: { children: React.ReactNode }) => (
  <div className="rb:flex rb:items-start">
    <ExclamationCircleFilled className="rb:mr-1 rb:mt-0.75" />
    {children}
  </div>
)

/**
 * Invite registration component
 */
const InviteRegister: React.FC = () => {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { token } = useParams();
  const { clearUserInfo, updateLoginInfo } = useUser();
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<LoginForm>();
  const { message } = App.useApp();
  const [passwordStrength, setPasswordStrength] = useState<'weak' | 'medium' | 'strong' | null>(null);
  const values = Form.useWatch<LoginForm>([], form);

  useEffect(() => {
    clearUserInfo();
    getInitalData()
  }, []);

  /** Fetch and validate invite token */
  const getInitalData = () => {
    if (!token) {
      message.warning(t('user.inviteLinkInvalid'))
      return
    }
    validateInviteToken(token).then((res) => {
      const response = res as ValidateToken
      form.setFieldsValue({
        email: response.email,
      })
    })
  }

  /** Validate password strength and return score */
  const validatePasswordStrength = (password: string): { strength: 'weak' | 'medium' | 'strong', error: string } => {
    let strength: 'weak' | 'medium' | 'strong' = 'weak';
    let score = 0;
    let error = '';

    // Password length check
    if (password.length < 8) {
      error = t('login.lengthDesc');
      return { strength, error };
    }
    score += 1;

    // Contains number
    if (/\d/.test(password)) score += 1;
    
    // Contains lowercase letter
    if (/[a-z]/.test(password)) score += 1;
    
    // Contains uppercase letter
    if (/[A-Z]/.test(password)) score += 1;
    
    // Contains special character
    if (/[^A-Za-z0-9]/.test(password)) score += 1;

    // Determine strength
    if (score >= 4) {
      strength = 'strong';
    } else if (score >= 3) {
      strength = 'medium';
    }

    // Return message based on strength
    if (strength === 'weak' && score >= 1) {
      error = t('login.weakDesc');
    } else if (strength === 'medium') {
      error = t('login.mediumDesc');
    }

    return { strength, error };
  };

  /** Update password strength indicator on change */
  const handlePasswordChange = (value: string) => {
    if (!value) {
      setPasswordStrength(null);
      return;
    }
    const { strength } = validatePasswordStrength(value);
    setPasswordStrength(strength);
  };

  /** Validate password confirmation matches */
  const validateConfirmPassword = (_: unknown, value: string) => {
    const password = values.password;
    if (!value) {
      return Promise.reject(new Error('Please confirm password'));
    }
    if (value !== password) {
      return Promise.reject(new Error('Passwords do not match'));
    }
    return Promise.resolve();
  };

  /** Handle registration form submission */
  const handleRegister: FormProps<LoginForm>['onFinish'] = async (values) => {
    setLoading(true);
    login({
      username: values.username,
      email: values.email,
      password: values.password,
      invite: token
    }).then((res) => {
      const response = res as LoginInfo;
      updateLoginInfo(response);
      navigate('/');
    }).finally(() => {
      setLoading(false);
    });
  };


  return (
    <div className="rb:w-screen rb:h-screen rb:flex rb:items-center rb:justify-center">
      <img src={inviteBg} className="rb:w-screen rb:h-screen rb:fixed rb:top-0 rb:left-0 rb:z-0" />

      <div className="rb:relative rb:z-1 rb:w-120 rb:max-h-full rb:overflow-y-auto rb:bg-[#FFFFFF] rb:rounded-xl rb:shadow-[0px_2px_10px_0px_rgba(11,49,124,0.2)]">
        <div className="rb:bg-[url('@/assets/images/login/inviteForm.png')] rb:bg-cover rb:bg-no-repeat rb:text-[24px] rb:font-bold rb:leading-8 rb:p-[28px_24px]">
          {t('login.welcomeTeam')}
          <div className="rb:text-[#5B6167] rb:text-[12px] rb:font-regular rb:leading-4 rb:mt-2.5">{t('login.welcomeTeamSubTitle')}</div>
        </div>
        <Form
          form={form}
          onFinish={handleRegister}
          layout="vertical"
          className={styles.form}
        >
          <RbAlert icon={<img src={checkBg} className="rb:w-6 rb:h-6" />} className="rb:mb-6">
            <div className="rb:text-[14px] rb:font-medium rb:leading-5">
              {t('login.invitationVerified')}
              <div className="rb:text-[12px] rb:font-regular rb:leading-4 rb:mt-1">{t('login.account')}: {values?.email || '-'}</div>
            </div>
          </RbAlert>
          <Form.Item 
            name="email" 
            label={t('login.emailAccount')}
            extra={<Extra>{t('login.emailAccountDesc')}</Extra>}
          >
            <Input disabled />
          </Form.Item>
          <Form.Item 
            name="password" 
            label={t('login.setPassword')}
            extra={
              <div>
                <div className="rb:mb-3">
                  <Progress 
                    percent={passwordStrength === 'weak' ? 33 : passwordStrength === 'medium' ? 66 : passwordStrength === 'strong' ? 100 : 0} 
                    steps={3} 
                    showInfo={false} 
                    style={{width: '100%'}}
                  />
                  <div className="rb:font-medium rb:mt-2">
                    {t('login.passwordStrength')}:
                    {passwordStrength
                      ? <span className="rb:text-[#155EEF]">{t(`login.${passwordStrength}`)}</span>
                      : <span className="rb:font-regular">{t('login.noSet')}</span>
                    }
                  </div>
                </div>
                <Extra>{t('login.setPasswordDesc')}</Extra>
              </div>
            }
            rules={[
              { required: true, message: t('login.setPasswordPlaceholder') },
              {
                validator: (_, value) => {
                  if (!value) {
                    return Promise.reject(new Error(t('login.lengthDesc')));
                  }
                  const { error } = validatePasswordStrength(value);
                  if (error && value.length >= 8) {
                    return Promise.resolve(); // 强度提示但不阻止提交
                  } else if (error) {
                    return Promise.reject(new Error(error));
                  }
                  return Promise.resolve();
                },
                validateTrigger: ['blur']
              }
            ]}
          >
            <Input.Password
              placeholder={t('login.setPasswordPlaceholder')}
              onChange={(e) => handlePasswordChange(e.target.value)}
              onBlur={(e) => handlePasswordChange(e.target.value)}
            />
          </Form.Item>
          <Form.Item 
            name="confirmPassword" 
            label={t('login.confirmPassword')}
            rules={[
              { required: true, message: t('login.confirmPasswordPlaceholder') },
              { validator: validateConfirmPassword }
            ]}
          >
            <Input.Password
              placeholder={t('login.confirmPasswordPlaceholder')}
            />
          </Form.Item>
          <Form.Item 
            name="username"
            label={<>{t('login.name')}<span className="rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-5"> {t('login.nameSubTitle')}</span></>}
          >
            <Input placeholder={t('login.namePlaceholder')} />
          </Form.Item>
          <Button
            type="primary"
            block
            loading={loading}
            htmlType="submit"
            className="rb:h-10! rb:rounded-lg!"
          >
            {t('login.register')}
          </Button>
        </Form>
      </div>
    </div>
  );
};

export default InviteRegister;