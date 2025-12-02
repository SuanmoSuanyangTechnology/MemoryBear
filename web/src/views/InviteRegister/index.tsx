import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { Button, Input, Form, Progress, App } from 'antd';
import { ExclamationCircleFilled } from '@ant-design/icons';
import { useUser, type LoginInfo } from '@/store/user';
import type { FormProps } from 'antd';
import { login } from '@/api/user'
import inviteBg from '@/assets/images/login/inviteBg.png'
import checkBg from '@/assets/images/login/checkBg.png'
import type { LoginForm, ValidateToken } from './types';
import { validateInviteToken } from '@/api/member'
import RbAlert from '@/components/RbAlert'
import styles from './index.module.css'

const Extra = ({ children }: { children: React.ReactNode }) => (
  <div className="rb:flex rb:items-start">
    <ExclamationCircleFilled className="rb:mr-[4px] rb:mt-[3px]" />
    {children}
  </div>
)

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

  // 密码强度校验函数
  const validatePasswordStrength = (password: string): { strength: 'weak' | 'medium' | 'strong', error: string } => {
    let strength: 'weak' | 'medium' | 'strong' = 'weak';
    let score = 0;
    let error = '';

    // 密码长度检查
    if (password.length < 8) {
      error = t('login.lengthDesc');
      return { strength, error };
    }
    score += 1;

    // 包含数字
    if (/\d/.test(password)) score += 1;
    
    // 包含小写字母
    if (/[a-z]/.test(password)) score += 1;
    
    // 包含大写字母
    if (/[A-Z]/.test(password)) score += 1;
    
    // 包含特殊字符
    if (/[^A-Za-z0-9]/.test(password)) score += 1;

    // 判断强度
    if (score >= 4) {
      strength = 'strong';
    } else if (score >= 3) {
      strength = 'medium';
    }

    // 根据强度返回提示
    if (strength === 'weak' && score >= 1) {
      error = t('login.weakDesc');
    } else if (strength === 'medium') {
      error = t('login.mediumDesc');
    }

    return { strength, error };
  };

  // 监听密码变化，更新强度
  const handlePasswordChange = (value: string) => {
    if (!value) {
      setPasswordStrength(null);
      return;
    }
    const { strength } = validatePasswordStrength(value);
    setPasswordStrength(strength);
  };

  // 密码一致性校验
  const validateConfirmPassword = (_: unknown, value: string) => {
    const password = values.password;
    if (!value) {
      return Promise.reject(new Error('请确认密码'));
    }
    if (value !== password) {
      return Promise.reject(new Error('两次输入的密码不一致'));
    }
    return Promise.resolve();
  };

  // 处理注册提交
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
      <img src={inviteBg} className="rb:w-screen rb:h-screen rb:fixed rb:top-0 rb:left-0 rb:z-[0]" />

      <div className="rb:relative rb:z-[1] rb:w-[480px] rb:max-h-full rb:overflow-y-auto rb:bg-[#FFFFFF] rb:rounded-[12px] rb:shadow-[0px_2px_10px_0px_rgba(11,49,124,0.2)]">
        <div className="rb:bg-[url('@/assets/images/login/inviteForm.png')] rb:bg-cover rb:bg-no-repeat rb:text-[24px] rb:font-bold rb:leading-[32px] rb:p-[28px_24px]">
          {t('login.welcomeTeam')}
          <div className="rb:text-[#5B6167] rb:text-[12px] rb:font-regular rb:leading-[16px] rb:mt-[10px]">{t('login.welcomeTeamSubTitle')}</div>
        </div>
        <Form
          form={form}
          onFinish={handleRegister}
          layout="vertical"
          className={styles.form}
        >
          <RbAlert icon={<img src={checkBg} className="rb:w-[24px] rb:h-[24px]" />} className="rb:mb-[24px]">
            <div className="rb:text-[14px] rb:font-medium rb:leading-[20px]">
              {t('login.invitationVerified')}
              <div className="rb:text-[12px] rb:font-regular rb:leading-[16px] rb:mt-[4px]">{t('login.account')}: {values?.email || '-'}</div>
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
                <div className="rb:mb-[12px]">
                  <Progress 
                    percent={passwordStrength === 'weak' ? 33 : passwordStrength === 'medium' ? 66 : passwordStrength === 'strong' ? 100 : 0} 
                    steps={3} 
                    showInfo={false} 
                    style={{width: '100%'}}
                  />
                  <div className="rb:font-medium rb:mt-[8px]">
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
            label={<>{t('login.name')}<span className="rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-[20px]"> {t('login.nameSubTitle')}</span></>}
          >
            <Input placeholder={t('login.namePlaceholder')} />
          </Form.Item>
          <Button
            type="primary"
            block
            loading={loading}
            htmlType="submit"
            className="rb:h-[40px]! rb:rounded-[8px]!"
          >
            {t('login.register')}
          </Button>
        </Form>
      </div>
    </div>
  );
};

export default InviteRegister;