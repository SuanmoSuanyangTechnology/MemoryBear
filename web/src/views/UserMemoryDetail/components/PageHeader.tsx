import { type FC, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { Layout } from 'antd';
import { useTranslation } from 'react-i18next';
import logoutIcon from '@/assets/images/logout.svg'

const { Header } = Layout;

interface ConfigHeaderProps {
  name?: string;
  operation: ReactNode
}
const PageHeader: FC<ConfigHeaderProps> = ({ 
  name,
  operation
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const goBack = () => {
    navigate('/user-memory', { replace: true })
  }
  return (
    <Header className="rb:w-full rb:h-16 rb:flex rb:justify-between rb:p-[16px_16px_16px_24px]! rb:border-b rb:border-[#EAECEE] rb:leading-8">
      <div className="rb:h-8 rb:flex rb:items-center rb:font-medium">
        {t('userMemory.memoryWindow', { name: name })}
        {operation}
      </div>

      <div className="rb:h-8 rb:flex rb:items-center rb:justify-end rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:cursor-pointer" onClick={goBack}>
        <img src={logoutIcon} className="rb:mr-2 rb:w-4 rb:h-4" />
        {t('common.return')}
      </div>
    </Header>
  );
};

export default PageHeader;