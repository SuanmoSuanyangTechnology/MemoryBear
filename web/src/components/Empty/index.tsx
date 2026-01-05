import { type FC } from 'react';
import { useTranslation } from 'react-i18next';
import emptyIcon from '@/assets/images/empty/empty.svg';

interface EmptyProps {
  url?: string;
  size?: number | number[];
  title?: string;
  isNeedSubTitle?: boolean;
  subTitle?: string;
  className?: string;
}
const  Empty: FC<EmptyProps> = ({
  url,
  size = 200,
  title,
  isNeedSubTitle = true,
  subTitle,
  className = '',
}) => {
  const { t } = useTranslation();
  const width = Array.isArray(size) ? size[0] : size ? size : url ? 200 : 88;
  const height = Array.isArray(size) ? size[1] : size ? size : url ? 200 : 88;
  
  const curSubTitle = isNeedSubTitle ? (subTitle || t('empty.tableEmpty')) : null;
  return (
    <div className={`rb:flex rb:items-center rb:justify-center rb:flex-col ${className}`}>
      <img src={url || emptyIcon} alt="404" style={{ width: `${width}px`, height: `${height}px` }} />
      {title && <div className="rb:mt-2 rb:leading-5">{title}</div>}
      {curSubTitle && <div className={`rb:mt-[${url ? 8 : 5}px] rb:leading-4 rb:text-[12px] rb:text-[#A8A9AA]`}>{curSubTitle}</div>}
    </div>
  );
}
export default Empty;