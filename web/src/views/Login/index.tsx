import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Input, Form, App } from 'antd';
import { useUser, type LoginInfo } from '@/store/user';
import type { FormProps } from 'antd';
import { login } from '@/api/user'
import loginBg from '@/assets/images/login/loginBg.png'
import check from '@/assets/images/login/check.png'
import email from '@/assets/images/login/email.svg'
import lock from '@/assets/images/login/lock.svg'
import type { LoginForm } from './types';

const inputClassName = "rb:rounded-[8px]! rb:p-[12px]! rb:h-[44px]!"
const LoginPage: React.FC = () => {
  const { t } = useTranslation();
  const { clearUserInfo, updateLoginInfo, getUserInfo } = useUser();
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<LoginForm>();
  const { message } = App.useApp();

  useEffect(() => {
    clearUserInfo();
  }, []);

  // 处理登录提交
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
        <div className="rb:absolute rb:top-[56px] rb:left-[64px]">
          <div className="rb:text-[28px] rb:leading-[33px] rb:font-bold rb:font-[AlimamaShuHeiTi,AlimamaShuHeiTi] rb:mb-[16px]">{t('login.title')}</div>
          <div className="rb:text-[18px] rb:leading-[25px] rb:font-regular">{t('login.subTitle')}</div>
        </div>

        <div className="rb:absolute rb:bottom-[81px] rb:left-[64px] rb:grid rb:grid-cols-2 rb:gap-x-[120px] rb:gap-y-[43px]">
          {['intelligentMemory', 'instantRecall', 'knowledgeAssociation'].map(key => (
            <div key={key} className="rb:flex">
              <img src={check} className="rb:w-[16px] rb:h-[16px] rb:mr-[8px] rb:mt-[3px]" />
              <div className="rb:text-[16px] rb:leading-[22px]">
                <div className="rb:font-medium">{t(`login.${key}`)}</div>
                <div className="rb:text-[#5B6167] rb:text-[14px] rb:leading-[20px] rb:font-regular! rb:mt-[8px]">{t(`login.${key}Desc`)}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="rb:bg-[#FFFFFF] rb:flex rb:items-center rb:justify-center rb:flex-[1_1_auto]">
        <div className="rb:w-[400px] rb:mx-auto">
          <div className="rb:text-center rb:text-[28px] rb:font-semibold rb:leading-[32px] rb:mb-[48px]">{t('login.welcome')}</div>
          <Form
            form={form}
            onFinish={handleLogin}
          >
            <Form.Item name="email" className="rb:mb-[20px]!">
              <Input
                prefix={<img src={email} className="rb:w-[20px] rb:h-[20px] rb:mr-[8px]" />}
                placeholder={t('login.emailPlaceholder')}
                className={inputClassName}
              />
            </Form.Item>
            <Form.Item name="password">
              <Input.Password
                prefix={<img src={lock} className="rb:w-[20px] rb:h-[20px] rb:mr-[8px]" />}
                placeholder={t('login.passwordPlaceholder')}
                className={inputClassName}
              />
            </Form.Item>
            <Button
              type="primary"
              block
              loading={loading}
              htmlType="submit"
              className="rb:h-[40px]! rb:rounded-[8px]! rb:mt-[16px]"
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