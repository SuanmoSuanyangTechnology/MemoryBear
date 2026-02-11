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

import { useUser, type LoginInfo } from '@/store/user';
import { login } from '@/api/user'
import loginBg from '@/assets/images/login/loginBg.png'
import check from '@/assets/images/login/check.png'
import email from '@/assets/images/login/email.svg'
import lock from '@/assets/images/login/lock.svg'
import type { LoginForm } from './types';

/**
 * Input field styling
 */
const inputClassName = "rb:rounded-[8px]! rb:p-[12px]! rb:h-[44px]!"

/**
 * Login page component
 */const LoginPage: React.FC = () => {
  const { t } = useTranslation();
  const { clearUserInfo, updateLoginInfo, getUserInfo } = useUser();
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<LoginForm>();
  const { message } = App.useApp();

  useEffect(() => {
    clearUserInfo();
  }, []);

  /** Handle login form submission */
  const handleLogin: FormProps<LoginForm>['onFinish'] = async (values) => {
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
    <div className="rb:min-h-screen rb:flex rb:h-screen">
      <div className="rb:relative rb:w-1/2 rb:h-screen rb:overflow-hidden">
        <img src={loginBg} alt="loginBg" className="rb:w-full rb:h-full rb:object-cover rb:absolute rb:top-1/2 rb:-translate-y-1/2 rb:left-0" />
        <div className="rb:absolute rb:top-14 rb:left-16">
          <div className="rb:text-[28px] rb:leading-8.25 rb:font-bold rb:font-[AlimamaShuHeiTi,AlimamaShuHeiTi] rb:mb-4">{t('login.title')}</div>
          <div className="rb:text-[18px] rb:leading-6.25 rb:font-regular">{t('login.subTitle')}</div>
        </div>

        <div className="rb:absolute rb:bottom-20.25 rb:left-16 rb:grid rb:grid-cols-2 rb:gap-x-30 rb:gap-y-10.75">
          {['intelligentMemory', 'instantRecall', 'knowledgeAssociation'].map(key => (
            <div key={key} className="rb:flex">
              <img src={check} className="rb:w-4 rb:h-4 rb:mr-2 rb:mt-0.75" />
              <div className="rb:text-[16px] rb:leading-5.5">
                <div className="rb:font-medium">{t(`login.${key}`)}</div>
                <div className="rb:text-[#5B6167] rb:text-[14px] rb:leading-5 rb:font-regular! rb:mt-2">{t(`login.${key}Desc`)}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="rb:bg-[#FFFFFF] rb:flex rb:items-center rb:justify-center rb:flex-[1_1_auto]">
        <div className="rb:w-100 rb:mx-auto">
          <div className="rb:text-center rb:text-[28px] rb:font-semibold rb:leading-8 rb:mb-12">{t('login.welcome')}</div>
          <Form
            form={form}
            onFinish={handleLogin}
          >
            <Form.Item name="email" className="rb:mb-5!">
              <Input
                prefix={<img src={email} className="rb:w-5 rb:h-5 rb:mr-2" />}
                placeholder={t('login.emailPlaceholder')}
                className={inputClassName}
              />
            </Form.Item>
            <Form.Item name="password">
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
              className="rb:h-10! rb:rounded-lg! rb:mt-4"
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