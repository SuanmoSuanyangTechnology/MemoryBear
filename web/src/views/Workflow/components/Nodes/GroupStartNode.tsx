import clsx from 'clsx';
import type { ReactShapeConfig } from '@antv/x6-react-shape';
import startIcon from '@/assets/images/workflow/start.png';

const GroupStartNode: ReactShapeConfig['component'] = () => {
  return (
    <div className={clsx('rb:cursor-pointer rb:group rb:relative rb:h-full rb:w-full rb:p-2.5 rb:border rb:rounded-xl rb:bg-white rb:hover:shadow-[0px_2px_6px_0px_rgba(33,35,50,0.12)] rb:border-[#DFE4ED]')}>
      <img src={startIcon} className="rb:w-6 rb:h-6" />
    </div>
  );
};

export default GroupStartNode;