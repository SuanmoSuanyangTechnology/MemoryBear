/**
 * v1 knowledge base card: prototype style (icon + name + description + document count + parent folder + more menu)
 */
import { Dropdown, Flex } from 'antd';
import type { MenuProps } from 'antd';
import { useTranslation } from 'react-i18next';
import {
  ShareAltOutlined,
} from '@ant-design/icons';

import { getTypeIcon } from '@/views/KnowledgeBase/constants';
import type { V1KnowledgeListItem } from '@/views/KnowledgeBase/typesV1';

interface KbV1CardProps {
  item: V1KnowledgeListItem;
  onOpen: (item: V1KnowledgeListItem) => void;
  menuItems: MenuProps['items'];
}

const KbV1Card = ({ item, onOpen, menuItems }: KbV1CardProps) => {
  const { t } = useTranslation();
  const location = item.folder_path?.length
    ? item.folder_path[item.folder_path.length - 1].name
    : t('knowledgeBase.root');

  const sources = item.processing_configuration?.sources;
  const sourceType = item.type === 'Folder'
    ? 'folder'
    : sources?.third_party
      ? 'third-party'
      : sources?.website
        ? 'web'
        : 'general';
  const typeIcon = getTypeIcon(sourceType);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onOpen(item)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') onOpen(item);
      }}
      className="rb:relative rb:rounded-xl rb:border rb:border-gray-200 rb:bg-white rb:p-4! rb:cursor-pointer rb:transition-shadow rb:hover:shadow-md"
    >
      <Flex align="flex-start" justify="space-between" gap={8}>
        <Flex align="center" gap={8} className="rb:min-w-0">
          <Flex align="center" justify="center" className="rb:size-8 rb:bg-[#f1f2f5] rb:rounded-lg">
            <span className="rb:size-5 rb:shrink-0 rb:bg-cover" style={{ backgroundImage: `url(${typeIcon})` }} />
          </Flex>
          <span className="rb:truncate rb:text-[15px] rb:font-medium rb:text-gray-800" title={item.name}>
            {item.name}
          </span>
        </Flex>
        <div onClick={(event) => event.stopPropagation()}>
          <Dropdown menu={{ items: menuItems }} placement="bottomRight" trigger={['click']}>
            <span
              role="button"
              tabIndex={0}
              className="rb:block rb:size-5 rb:bg-cover rb:bg-[url('@/assets/images/common/more.svg')] rb:cursor-pointer rb:hover:bg-[url('@/assets/images/common/more_hover.svg')]"
            />
          </Dropdown>
        </div>
      </Flex>

      <div className="rb:mt-3 rb:min-h-10 rb:text-[13px] rb:leading-5 rb:text-gray-500 rb:line-clamp-2" title={item.description ?? ''}>
        {item.description || t('knowledgeBase.noDescription')}
      </div>

      <Flex justify="space-between" align="center" className="rb:mt-3! rb:border-t rb:border-gray-100 rb:pt-3! rb:text-[12px] rb:text-gray-600">
        <Flex align="center" gap={4}>
          <span className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/application/text_gray.svg')]" />
          <span>{item.doc_num ?? 0}</span>
        </Flex>
        {item.is_shared
          ? (
            <Flex align="center" gap={4} className="rb:text-gray-500!">
              <ShareAltOutlined />
              <span>{t('knowledgeBase.fromOtherSpace')}</span>
            </Flex>
          )
          : (
            <Flex align="center" gap={4}>
              <span className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/knowledgeBase/folder_light_gray.svg')]" />
              <span className="rb:text-gray-500" title={location}>{location}</span>
            </Flex>
          )
        }
      </Flex>
    </div>
  );
};

export default KbV1Card;
