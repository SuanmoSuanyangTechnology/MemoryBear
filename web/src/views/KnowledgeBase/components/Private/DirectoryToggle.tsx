import type { FC } from 'react';
import { Tooltip } from 'antd';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';

interface DirectoryToggleProps {
  directoryCollapsed: boolean;
  onToggleDirectory: () => void;
}

const DirectoryToggle: FC<DirectoryToggleProps> = ({ directoryCollapsed, onToggleDirectory }) => {
  const { t } = useTranslation();
  const label = t(`knowledgeBase.${directoryCollapsed ? 'expandDirectory' : 'collapseDirectory'}`);

  return (
    <Tooltip title={label}>
      <div
        className={clsx(
          "rb:size-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/menuNew/menuFold.svg')] rb:transition-transform rb:duration-500 rb:ease-in-out",
          directoryCollapsed && 'rb:rotate-180',
        )}
        onClick={onToggleDirectory}
      />
    </Tooltip>
  );
};

export default DirectoryToggle;
