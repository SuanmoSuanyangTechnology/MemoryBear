import clsx from 'clsx';
import { useTranslation } from 'react-i18next'
import type { ReactShapeConfig } from '@antv/x6-react-shape';

const NormalNode: ReactShapeConfig['component'] = ({ node }) => {
  const data = node?.getData() || {}
  const { t } = useTranslation()

  return (
    <div className={clsx('rb:cursor-pointer rb:group rb:relative rb:h-16 rb:w-60 rb:p-2.5 rb:border rb:rounded-xl rb:bg-white rb:hover:shadow-[0px_2px_6px_0px_rgba(33,35,50,0.12)]', {
      'rb:border-[#155EEF]': data.isSelected,
      'rb:border-[#DFE4ED]': !data.isSelected
    })}>
      <div className="rb:flex rb:items-center rb:justify-between">
        <div className="rb:flex rb:items-center rb:gap-2 rb:flex-1">
          <img src={data.icon} className="rb:w-5 rb:h-5" />
          <div className="rb:wrap-break-word rb:line-clamp-1">{data.name ?? t(`workflow.${data.type}`)}</div>
        </div>
        
        <div 
          className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]" 
          onClick={(e) => {
            e.stopPropagation()
            node.remove()
          }}
        ></div>
      </div>

      <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4 rb:mt-1.5">{t('workflow.clickToConfigure')}</div>
    </div>
  );
};

export default NormalNode;