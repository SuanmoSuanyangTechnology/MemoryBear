import { type FC } from 'react';
import { useTranslation } from 'react-i18next'
import clsx from 'clsx';
import { Node } from '@antv/x6';
import { Flex, Dropdown, type MenuProps } from 'antd';

const NodeTools: FC<{ node: Node }> = ({
  node
}) => {
  const data = node?.getData() || {};
  const { t } = useTranslation()

  const handleClick: MenuProps['onClick'] = (e) => {
    switch (e.key) {
      case 'delete':
        node.remove()
        break;
      case 'copy':
        break;
    }
  }
  return (
    <div className={clsx("rb:absolute rb:p-1 rb:bg-white rb:-top-7.5 rb:right-0 rb:rounded-lg", {
      'rb:block': data.isSelected,
      'rb:hidden': !data.isSelected
    })}>
      <Dropdown
        menu={{
          items: [
            { key: 'delete', icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/delete_dark.svg')]"></div>, label: <Flex>{t('common.delete')}</Flex>},
            // { key: 'copy', icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/copy_dark.svg')]"></div>, label: t('common.copy') }
          ],
          onClick: handleClick
        }}
      >
        <div className="rb:cursor-pointer rb:size-4 rb:hover:bg-[#F6F6F6] rb:rounded-sm rb:bg-cover rb:bg-[url(@/assets/images/common/dash.svg)]">
        </div>
      </Dropdown>
    </div>
  )
}

export default NodeTools;