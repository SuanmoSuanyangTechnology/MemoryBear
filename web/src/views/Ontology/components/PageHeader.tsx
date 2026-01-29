import { type FC, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { Layout, Button } from 'antd';
import { useTranslation } from 'react-i18next';
import logoutIcon from '@/assets/images/logout_hover.svg'

const { Header } = Layout;

interface ConfigHeaderProps {
  name?: string;
  subTitle?: ReactNode | string;
  extra?: ReactNode;
}
const PageHeader: FC<ConfigHeaderProps> = ({ 
  name,
  subTitle,
  extra
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const goBack = () => {
    navigate(-1)
  }
  return (
    <Header className="rb:w-full rb:h-16 rb:flex rb:justify-between rb:p-[0_16px_0_24px]! rb:border-b rb:border-[#EAECEE] rb:leading-8">
      <div className="rb:flex rb:flex-col rb:justify-center rb:gap-1 rb:mr-4">
        <div className="rb:text-[16px] rb:leading-6 rb:font-medium">
          {name}
        </div>
        <div className="rb:text-[12px] rb:text-[#5B6167] rb:leading-4">{subTitle}</div>
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