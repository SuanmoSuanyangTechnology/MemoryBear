/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:40:01 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-03 16:40:32
 */
/**
 * Login Page
 * Handles user authentication and login
 * Features split-screen design with branding and login form
 */

import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Input, Form, App } from 'antd';
import type { FormProps } from 'antd';
import clsx from 'clsx';

import { useUser, type LoginInfo } from '@/store/user';
import { login } from '@/api/user'
import loginBg from '@/assets/images/login/bg.mp4'
import check from '@/assets/images/login/check.svg'
import email from '@/assets/images/login/email.svg'
import lock from '@/assets/images/login/lock.svg'
import type { LoginForm } from './types';
import { useI18n } from '@/store/locale'

/**
 * Input field styling
 */
const inputClassName = "login-input rb:rounded-[8px]! rb:p-[12px]! rb:h-[44px]! rb:bg-transparent! rb:text-[#FFFFFF]! [&_input]:rb:text-[#FFFFFF]! [&_input]:rb:caret-[#FFFFFF]!"

/**
 * Login page component
 */const LoginPage: React.FC = () => {
  const { t } = useTranslation();
  const { clearUserInfo, updateLoginInfo, getUserInfo } = useUser();
  const { language } = useI18n()
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<LoginForm>();
  const emailVal = Form.useWatch('email', form);
  const passwordVal = Form.useWatch('password', form);
  const canLogin = !!(emailVal && passwordVal);
  const { message } = App.useApp();

  useEffect(() => {
    clearUserInfo();
  }, []);

  /** Handle login form submission */
  const handleLogin: FormProps<LoginForm>['onFinish'] = async (values) => {
    if (!canLogin) return;
    if (!values.email) {
      message.warning(t('login.emailPlaceholder'));
      return;
    }
    if (!values.password) {
      message.warning(t('login.passwordPlaceholder'));
      return;
    }
    
    setLoading(true);
    login(values).then((res) => {
      const response = res as LoginInfo;
      updateLoginInfo(response);
      getUserInfo(true)
    }).finally(() => {
      setLoading(false);
    });
  };


  return (
    <div className="rb:min-h-screen rb:flex rb:h-screen rb:bg-[#0A0A0A] rb:text-[#FFFFFF]">
      <div className="rb:relative rb:w-1/2 rb:h-screen rb:overflow-hidden">
        <video src={loginBg} loop autoPlay muted className="rb:w-full rb:h-full rb:object-cover"></video>
        <div className="rb:absolute rb:top-10 rb:left-12">
          <div className={clsx("rb:h-8.25 rb:bg-cover", {
            "rb:w-89 rb:bg-[url('@/assets/images/login/title_en.png')]": language !== 'zh',
            "rb:w-42 rb:bg-[url('@/assets/images/login/title_zh.png')]": language === 'zh'
          })}></div>
          <div className="rb:text-[18px] rb:text-[rgba(255,255,255,0.7)] rb:leading-6.25 rb:font-regular rb:mt-3">{t('login.subTitle')}</div>
        </div>

        <div className="rb:absolute rb:bottom-14 rb:left-12 rb:right-12 rb:grid rb:grid-cols-2 rb:gap-x-30 rb:gap-y-10.75">
          {['intelligentMemory', 'instantRecall', 'knowledgeAssociation'].map((key, index) => (
            <div key={key} className={`rb:flex${index === 0 ? ' rb:col-span-2' : ''}`}>
              <img src={check} className="rb:w-4 rb:h-4 rb:mr-2 rb:mt-0.75" />
              <div className="rb:text-[16px] rb:leading-5.5">
                <div className="rb:font-medium">{t(`login.${key}`)}</div>
                <div className="rb:text-[14px] rb:text-[rgba(255,255,255,0.7)] rb:leading-5 rb:font-regular! rb:mt-2">{t(`login.${key}Desc`)}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="rb:flex rb:items-center rb:justify-center rb:flex-[1_1_auto]">
        <div className="rb:w-110 rb:mx-auto">
          <div className="rb:text-center rb:text-[24px] rb:font-[MiSans-Bold] rb:font-bold rb:leading-8 rb:mb-12">{t('login.welcome')}</div>
          <Form
            form={form}
            onFinish={handleLogin}
          >
            <Form.Item name="email" className="rb:mb-6!">
              <Input
                prefix={<img src={email} className="rb:w-5 rb:h-5 rb:mr-2" />}
                placeholder={t('login.emailPlaceholder')}
                className={inputClassName}
              />
            </Form.Item>
            <Form.Item name="password" className="rb:mb-0!">
              <Input.Password
                prefix={<img src={lock} className="rb:w-5 rb:h-5 rb:mr-2" />}
                placeholder={t('login.passwordPlaceholder')}
                className={inputClassName}
              />
            </Form.Item>
            <Button
              type="primary"
              block
              loading={loading}
              htmlType="submit"
              disabled={!canLogin}
              className={clsx("rb:h-11.5! rb:rounded-lg! rb:mt-12", {
                'rb:hover:bg-[#2d6ef1]! rb:bg-[#155EEF]! rb:border-[#155EEF]!': canLogin,
                'rb:bg-[#171719]! rb:border-[#171719]!': !canLogin
              })}
            >
              {t('login.loginIn')}
            </Button>
          </Form>
        </div>
      </div>
    </div>
  );
};

export default LoginPage;