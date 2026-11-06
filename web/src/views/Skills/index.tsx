import { useRef, useState } from 'react';
import { App, Button, Flex, Skeleton } from 'antd';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';

import PageTabs from '@/components/PageTabs';
import PageScrollList from '@/components/PageScrollList';
import RbCard from '@/components/RbCard';
import Tag from '@/components/Tag';
import SearchInput from '@/components/SearchInput';
import { getSkillStatistics, deleteSkill, getSkillListUrl, skillExport } from '@/api/skill';
import SkillDetail, { type SkillDetailRef } from './components/SkillDetail';
import SkillImport from './components/SkillImport';
import SkillIcon from './components/SkillIcon';
import { Kind, type Skill, type SkillQuery, type Statistic } from './types';
import { isPlugin } from './utils';
import MoreDropdown from '@/components/MoreDropdown'

type AllKind = Kind | 'all'

/** Skill list in resource center; pagination and search are handled by PageScrollList via API requests. */
const Skills = () => {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const [importOpen, setImportOpen] = useState(false);
  const detailRef = useRef<SkillDetailRef>(null);
  const [kind, setKind] = useState<AllKind>('all');
  const [search, setSearch] = useState<string | undefined>(undefined);
  const [version, setVersion] = useState(0);

  const [statistics, setStatistics] = useState<Statistic>({} as Statistic)
  const tabs: { value: AllKind; label: string; count: keyof Statistic; }[] = [
    { value: 'all', label: t('skills.all'), count: 'total' },
    { value: Kind.Plugin, label: t('skills.plugin'), count: 'plugin_count' },
    { value: Kind.Skill, label: t('skills.skill'), count: 'skill_count' },
  ];

  const performDelete = async (item: Skill) => {
    try {
      await deleteSkill(item.id);
      message.success(t('skills.deleteSuccess'));
      setVersion(current => current + 1);
    } catch {
      message.error(t('skills.deleteFailed'));
    }
  };

  const handleDelete = (item: Skill) => {
    const plugin = isPlugin(item);
    modal.confirm({
      title: t('skills.deleteTitle', { value0: plugin ? t('skills.plugin') : t('skills.skill') }),
      icon: null,
      content: (
        <div>
          <div>{t('skills.deleteConfirmPrefix')}{item.name}”?</div>
          {plugin && <div className="rb:text-xs rb:text-gray-600 rb:mt-2">{t('skills.deleteMembersPrefix')}{item.members?.length ?? 0} {t('skills.deleteMembersSuffix')}</div>}
        </div>
      ),
      okText: t('skills.confirmDelete'),
      okType: 'danger',
      cancelText: t('skills.cancel'),
      onOk: () => performDelete(item),
    });
  };
  const handleViewDetail = (item: Skill) => {
    detailRef.current?.handleOpen(item)
  }
  const handleExport = (item: Skill) => {
    skillExport(item.id, `${item.name}.zip`)
  }

  const getStatistics = useCallback(() => {
    getSkillStatistics({
      search: search?.trim() || undefined,
      is_active: true
    })
      .then(res => {
        setStatistics(res)
      })
  }, [search])

  useEffect(() => {
    getStatistics()
  }, [search, getStatistics])

  return (
    <Flex vertical gap={12} className="rb:h-full rb:min-h-0">
      <header className="rb:shrink-0 rb:ml-2">
        <h2 className="rb:m-0 rb:text-sm rb:font-medium">{t('skills.management')}</h2>
        <p className="rb:mb-0 rb:mt-1 rb:text-xs rb:text-gray-500">{t('skills.managementDescription')}</p>
      </header>
      <Flex justify="space-between" align="center" wrap gap={12} className="rb:shrink-0">
        <PageTabs
          value={kind}
          options={tabs.map(tab => ({
            value: tab.value,
            label: <>{tab.label} <span className="rb:text-[12px] rb:text-gray-600">{statistics[tab.count] ?? 0}</span></>,
          }))}
          onChange={value => {
            setKind(value as AllKind)
          }}
        />
        <Flex align="center" wrap gap={12}>
          <SearchInput
            value={search}
            placeholder={t('skills.searchPackage')}
            onSearch={setSearch}
          />
          <Button type="primary" onClick={() => setImportOpen(true)}>{t('skills.scrollRegion')}</Button>
        </Flex>
      </Flex>
      <Flex role="region" aria-label={t('skills.importPackageAction')} vertical className="rb:min-h-0 rb:flex-1">
        <PageScrollList<Skill, SkillQuery>
          key={JSON.stringify([kind, search, version])}
          url={getSkillListUrl}
          query={{
            ...(kind !== 'all' ? { kind } : {}),
            search: search?.trim() || undefined,
            is_active: true
          }}
          column={3}
          heightClass="rb:flex-1!"
          className="rb:flex-1!"
          pageLoading={
            <div className="rb:grid rb:grid-cols-3 rb:gap-3 rb:py-3" aria-busy="true">
              {Array.from({ length: 3 }, (_, index) => (
                <RbCard
                  key={index}
                  isNeedTooltip={false}
                  title={<Skeleton active title={{ width: '60%' }} paragraph={{ rows: 1, width: '30%' }} />}
                  avatarText={<Skeleton.Avatar active shape="square" size={44} />}
                  avatarClassName="rb:bg-transparent!"
                >
                  <div className="rb:mb-3 rb:h-12 rb:overflow-hidden rb:rounded-lg rb:bg-gray-100 rb:p-2">
                    <Skeleton active title={false} paragraph={{ rows: 2, width: ['100%', '75%'] }} />
                  </div>
                  <Skeleton active title={false} paragraph={{ rows: 1, width: '40%' }} />
                </RbCard>
              ))}
            </div>
          }
          renderItem={item => {
            const plugin = isPlugin(item);
            return (
              <RbCard
                title={(
                  <div className="rb:mr-2">
                    <Tooltip title={item.name}>
                      <div className="rb:whitespace-break-spaces rb:wrap-break-word rb:line-clamp-1">{item.name}</div>
                    </Tooltip>
                    <Tag color={plugin ? 'purple' : 'processing'}>{plugin ? t('skills.plugin') : t('skills.skill')}</Tag>
                  </div>
                )}
                isNeedTooltip={false}
                className="rb:h-full! rb:cursor-pointer"
                avatarText={<SkillIcon plugin={plugin} className="rb:size-11" iconClassName="rb:size-7" />}
                avatarClassName="rb:bg-transparent!"
                extra={<MoreDropdown
                items={[
                    {
                      key: 'view',
                      icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/eye.svg')]" />,
                      label: t('common.view'),
                      onClick: () => handleViewDetail(item),
                    },
                    {
                      key: 'export',
                      icon: <div className="rb:size-3.5 rb:bg-cover rb:bg-[url('@/assets/images/knowledgeBase/export.svg')]" />,
                      label: t('skills.exportPackage'),
                      onClick: () => handleExport(item),
                    },
                    {
                      key: 'delete',
                      danger: true,
                      icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/delete_red_big.svg')]" />,
                      label: t('common.delete'),
                      onClick: () => handleDelete(item),
                    },
                  ]}
                />}
                onClick={() => handleViewDetail(item)}
              >
                <div className="rb:mb-3 rb:overflow-hidden rb:h-12 rb:p-2 rb:text-[12px] rb:leading-4 rb:text-gray-600 rb:bg-gray-100 rb:rounded-lg">
                  <div className="rb:whitespace-break-spaces rb:wrap-break-word rb:line-clamp-2 rb:max-h-8">
                    {item.description || t('skills.noDescription')}
                  </div>
                </div>

                <div className="rb:text-gray-600 rb:text-[12px]">
                  {plugin ? t('skills.memberCount', { value0: item.members?.length ?? 0 }) : item.call_name || item.members?.[0]?.call_name || item.id}
                </div>
              </RbCard>
            );
          }}
        />
      </Flex>
      <SkillImport open={importOpen} onClose={() => setImportOpen(false)} onImported={() => setVersion(current => current + 1)} />
      <SkillDetail ref={detailRef} />
    </Flex>
  );
};

export default Skills;