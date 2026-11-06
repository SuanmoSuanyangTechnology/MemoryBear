import { useRef, useState, useEffect } from 'react';
import { Button, Flex, Tabs } from 'antd';
import { EyeOutlined, PlusOutlined, UserOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import { getAppAssetsUrl, getAppAssetsCategories } from '@/api/resourceCenter';
import PageScrollList from '@/components/PageScrollList';
import ApplicationDetailModal from './components/ApplicationDetailModal';
import type { ApplicationDetailModalRef } from './components/ApplicationDetailModal';
import AddToWorkspaceModal from './components/AddToWorkspaceModal';
import type { AddToWorkspaceModalRef } from './components/AddToWorkspaceModal';
import SearchInput from '@/components/SearchInput'
import RbCard from '@/components/RbCard';
import type { AppAssetItem, AppAssetsQuery, Categories } from './types'

/** Tenant application catalog with category filtering and progressive loading. */
const Applications = () => {
  const { t, i18n } = useTranslation();
  const [keyword, setKeyword] = useState('');
  const [category, setCategory] = useState('all');
  const detailModalRef = useRef<ApplicationDetailModalRef>(null);
  const addModalRef = useRef<AddToWorkspaceModalRef>(null);

  const [categories, setCategories] = useState<Categories[]>([]);
  useEffect(() => {
    let active = true;
    getAppAssetsCategories()
      .then(data => {
        if (active) setCategories([...data].sort((a, b) => a.sort - b.sort));
      })
      .catch(() => {
        if (active) setCategories([]);
      });
    return () => { active = false; };
  }, []);
  return (
    <Flex vertical className="rb:h-full rb:overflow-hidden!">
      <Flex align="center" justify="space-between" gap={24}>
        <div>
          <div className="rb:text-[15px] rb:font-[MiSans-Bold] rb:font-bold rb:leading-5.5">
            {t('resourceCenter.title')}
          </div>
          <div className="rb:text-gray-600 rb:text-[13px] rb:leading-5">
            {t('resourceCenter.subtitle')}
          </div>
        </div>
        <SearchInput
          allowClear
          value={keyword}
          onSearch={value => setKeyword(value)}
          placeholder={t('resourceCenter.search')}
        />
      </Flex>
      <Tabs
        activeKey={category}
        onChange={setCategory}
        items={[
          { key: 'all', label: t('resourceCenter.all') },
          ...categories.map(item => ({ key: item.code, label: i18n.language.startsWith('en') ? item.name_en : item.name })),
        ]}
      />
      <PageScrollList<AppAssetItem, AppAssetsQuery>
        key={JSON.stringify([keyword, category])}
        url={getAppAssetsUrl}
        query={{ q: keyword || undefined, category: category === 'all' ? undefined : category, pagesize: 9 }}
        heightClass="rb:flex-1!"
        renderItem={(item) => (
          <RbCard
            isNeedTooltip={false}
            avatarUrl={item.icon}
            avatarText={
              item.icon
              ? undefined
              : item.source_app_name.trim()[0]
            }
            avatarClassName={clsx({
              'rb:bg-[#155EEF]': !item.icon && item.source_app_type === 'agent',
              'rb:bg-[#9C6FFF]!': !item.icon && item.source_app_type === 'multi_agent',
              'rb:bg-primary-600': !item.icon && item.source_app_type.includes('workflow'),
              'rb:bg-transparent!': item.icon
            })}
            title={
              <div className="rb:flex-1!">
                <div className="rb:leading-5.5 rb:wrap-break-word rb:line-clamp-1">
                  {item.source_app_name}
                </div>
                <p className="rb:mt-2 rb:text-gray-600 rb:text-xs rb:font-normal!"><UserOutlined /> {item.publisher_name} · <EyeOutlined /> {item.view_count}</p>
              </div>
            }
            extra={<Button type="text" icon={<PlusOutlined />} aria-label={t('resourceCenter.add')} onClick={event => { event.stopPropagation(); addModalRef.current?.handleOpen(item.id); }} />}
            className="rb:group/card rb:cursor-pointer"
            onClick={() => detailModalRef.current?.handleOpen(item.id)}
          >
            <div className="rb:overflow-hidden rb:h-12 rb:p-2 rb:text-[12px] rb:leading-4 rb:text-gray-600 rb:bg-gray-100 rb:rounded-lg">
              <div className="rb:whitespace-break-spaces rb:wrap-break-word rb:line-clamp-2 rb:max-h-8">
                {item.description || t('resourceCenter.noDescription')}
              </div>
            </div>
          </RbCard>
        )}
      />
      <ApplicationDetailModal ref={detailModalRef} onAdd={(id) => addModalRef.current?.handleOpen(id)} />
      <AddToWorkspaceModal ref={addModalRef} />
    </Flex>
  );
}
export default Applications