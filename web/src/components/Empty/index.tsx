import { type FC } from 'react';
import { useTranslation } from 'react-i18next';
import emptyIcon from '@/assets/images/empty/empty.svg';

interface EmptyProps {
  url?: string;
  size?: number | number[];
  title?: string;
  subTitle?: string;
  className?: string;
}
const  Empty: FC<EmptyProps> = ({
  url,
  size = 200,
  title,
  subTitle,
  className = '',
}) => {
  const { t } = useTranslation();
  const width = Array.isArray(size) ? size[0] : size ? size : url ? 200 : 88;
  const height = Array.isArray(size) ? size[1] : size ? size : url ? 200 : 88;
  
  subTitle = subTitle || t('empty.tableEmpty');
  return (
    <div className={`rb:flex rb:items-center rb:justify-center rb:flex-col ${className}`}>
      <img src={url || emptyIcon} alt="404" style={{ width: `${width}px`, height: `${height}px` }} />
      {title && <div className="rb:mt-[8px] rb:leading-[20px]">{title}</div>}
      {subTitle && <div className={`rb:mt-[${url ? 8 : 5}px] rb:leading-[16px] rb:text-[#5B6167]`}>{subTitle}</div>}
    </div>
  );
}
export default Empty;