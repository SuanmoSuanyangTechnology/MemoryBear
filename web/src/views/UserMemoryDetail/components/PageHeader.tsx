/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:30 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 18:32:30 
 */
/**
 * Page Header Component
 * Header with navigation and operation buttons
 */

import { type FC, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { Layout, Button } from 'antd';
import { useTranslation } from 'react-i18next';

import logoutIcon from '@/assets/images/logout_hover.svg'

const { Header } = Layout;

/**
 * Component props
 */
interface ConfigHeaderProps {
  name?: string;
  operation?: ReactNode;
  source?: 'detail' | 'node';
  extra?: ReactNode;
}
const PageHeader: FC<ConfigHeaderProps> = ({ 
  name,
  operation,
  source = 'detail',
  extra
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();

  /** Navigate back */
  const goBack = () => {
    if (source === 'detail') {
      navigate('/user-memory', { replace: true })
    } else {
      navigate(-1)
    }
  }
  return (
    <Header className="rb:w-full rb:h-16 rb:flex rb:justify-between rb:p-[16px_16px_16px_24px]! rb:border-b rb:border-[#EAECEE] rb:leading-8">
      <div className="rb:h-8 rb:flex rb:items-center rb:font-medium">
        {t('userMemory.memoryWindow', { name: name })}
        {operation}
      </div>

      <div className="rb:flex rb:items-center rb:gap-3">
        <Button type="primary" ghost className="rb:h-6! rb:px-2! rb:leading-5.5!" onClick={goBack}>
          <img src={logoutIcon} className="rb:w-4 rb:h-4" />
          {t('common.return')}
        </Button>
        {extra}
      </div>
    </Header>
  );
};

export default PageHeader;