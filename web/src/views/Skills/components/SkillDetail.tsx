import { forwardRef, useImperativeHandle, useRef, useState } from 'react';
import { Button, Skeleton, Tag, Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import { getSkillDetail } from '@/api/skill';
import { Kind, SkillFormat, type MemberDetail, type Skill, type SkillDetail as SkillDetailData } from '../types';
import ToolList from './ToolList/ToolList';
import RbModal from '@/components/RbModal';
import SkillFiles from './SkillFiles';
import Empty from '@/components/Empty';
import pluginIcon from '@/assets/images/menuNew/plugin.svg';
import skillIcon from '@/assets/images/menuNew/skill.svg';

export interface SkillDetailRef { handleOpen: (skill: Skill) => void }

const SkillDetail = forwardRef<SkillDetailRef>((_props, ref) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<SkillDetailData | null>(null);
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [selectedKind, setSelectedKind] = useState<Kind | undefined>();
  const [loading, setLoading] = useState(false);
  const [member, setMember] = useState<MemberDetail | null>(null);
  const [memberDetail, setMemberDetail] = useState<SkillDetailData | null>(null);
  const requestId = useRef(0);

  const handleClose = () => {
    requestId.current++;
    setOpen(false);
    setDetail(null);
    setSelectedSkill(null);
    setSelectedKind(undefined);
    setMember(null);
    setMemberDetail(null);
  };
  const handleOpen = (skill: Skill) => {
    const id = ++requestId.current;
    setOpen(true);
    setSelectedKind(skill.kind);
    setSelectedSkill(skill);
    setDetail(null);
    setMember(null);
    setMemberDetail(null);
    setLoading(true);
    getSkillDetail(skill.id).then(result => {
      if (id === requestId.current) setDetail(result as SkillDetailData);
    }).finally(() => {
      if (id === requestId.current) setLoading(false);
    });
  };
  const showMember = (item: MemberDetail) => {
    setMember(item);
    setMemberDetail(null);
    if (!item.id) return;
    const id = ++requestId.current;
    setLoading(true);
    getSkillDetail(item.id).then(result => {
      if (id === requestId.current) setMemberDetail(result as SkillDetailData);
    }).finally(() => {
      if (id === requestId.current) setLoading(false);
    });
  };
  const back = () => {
    requestId.current++;
    setMember(null);
    setMemberDetail(null);
    setLoading(false);
  };
  useImperativeHandle(ref, () => ({ handleOpen }));

  const current = member ? memberDetail : detail;
  const files = (current?.files ?? []).filter(item => item.rel_path).map(item => ({
    path: item.rel_path!, content: item.content, is_binary: item.is_binary,
  }));
  if (current?.skill_md && !files.some(item => item.path === 'SKILL.md')) {
    files.unshift({ path: 'SKILL.md', content: current.skill_md, is_binary: false });
  }
  const plugin = (detail?.kind ?? selectedKind) === Kind.Plugin;
  const heading = member ? memberDetail ?? member : detail ?? selectedSkill;
  const legacy = (current?.skill_format ?? (!member ? selectedSkill?.skill_format : undefined)) === SkillFormat.Legacy;

  return (
    <RbModal
      open={open}
      onCancel={handleClose}
      classNames={{
        header: 'rb:mb-5'
      }}
      width={1000}
      title={(
        <div>
          {member &&
            <Button type="link" className="rb:mb-2! rb:group" onClick={back}
              icon={<div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up_blue.svg')] rb:-rotate-90 rb:group-hover:opacity-65" />}
            >
              {t('skills.backPrefix')} {detail?.name ?? t('skills.pluginSkills')}
            </Button>
          }
          <Flex align="center" gap={12} className="">
            <Flex component="span" align="center" justify="center" className={` rb:size-11 rb:flex-none rb:rounded-[10px] ${plugin && !member ? 'rb:bg-purple-50' : 'rb:bg-blue-700'}`}>
              <img className="rb:size-9" src={plugin && !member ? pluginIcon : skillIcon} alt="" />
            </Flex>
            <div className="rb:min-w-0">
              <Flex align="center" gap={8} className="rb:text-[16px] rb:font-semibold">
                <span>{heading?.name}</span>
                <Tag className="rb:m-0! rb:font-normal" color={plugin && !member ? 'purple' : 'blue'}>{plugin && !member ? t('skills.plugin') : t('skills.skill')}</Tag>
              </Flex>
              <div className="rb:mt-1.5 rb:text-gray-600 rb:text-[12px] rb:font-normal">{heading?.call_name || heading?.name}</div>
            </div>
          </Flex>
        </div>
      )}
      footer={null}
    >
      {loading
        ? (
          <Flex vertical gap={12} aria-busy="true">
            <div className="rb:rounded-lg rb:bg-gray-100 rb:px-3 rb:py-3.5">
              <Skeleton active title={false} paragraph={{ rows: 2 }} />
            </div>
            {plugin && !member
              ? (
                <div className="rb:grid rb:grid-cols-2 rb:gap-3 rb:[@media(max-width:600px)]:grid-cols-1">
                  {Array.from({ length: 2 }, (_, index) => (
                    <div key={index} className="rb:min-w-0 rb:rounded-lg rb:border rb:border-gray-400 rb:p-3.5">
                      <Skeleton active title={{ width: '40%' }} paragraph={{ rows: 2 }} />
                    </div>
                  ))}
                </div>
              )
              : legacy ? (
                <Flex vertical gap={12}>
                  <section aria-label={t('skills.mainfest')}>
                    <div className="rb:mb-2">{t('skills.keywords')}</div>
                    <Flex wrap gap={4}>
                      {[80, 64, 96].map(width => (
                        <Skeleton.Button key={width} active size="small" style={{ width }} />
                      ))}
                    </Flex>
                  </section>
                  <div>
                    <div>{t('skills.promptConfiguration')}</div>
                    <div className="rb:mt-2 rb:rounded-xl rb:bg-gray-100 rb:p-2">
                      <Skeleton active title={false} paragraph={{ rows: 3 }} />
                    </div>
                  </div>
                  <div className="rb:rounded-xl rb:border rb:border-gray-400 rb:p-4">
                    <Skeleton active title={{ width: '20%' }} paragraph={{ rows: 2 }} />
                  </div>
                </Flex>
              ) : (
                <div className="rb:h-96 rb:overflow-hidden rb:rounded-xl rb:border rb:border-gray-400 rb:p-4">
                  <Skeleton active title={{ width: '30%' }} paragraph={{ rows: 8 }} />
                </div>
              )}
          </Flex>
        )
        : current
        ? (
          <Flex vertical gap={12}>
            <div className="rb:px-3 rb:py-3.5 rb:rounded-lg rb:bg-gray-100 rb:text-gray-600 rb:text-[13px] rb:font-normal rb:leading-5">{current.description || t('skills.noDescription')}</div>
            {plugin && !member && (
              <section aria-label={t('skills.includedSkills')}>
                <h3 className="rb:m-0 rb:mb-3 rb:text-[14px] rb:font-semibold">{t('skills.containsPrefix')}{detail?.members?.length ?? 0} {t('skills.skillsSuffix')}</h3>
                {detail?.members?.length
                  ? <div className="rb:grid rb:grid-cols-2 rb:gap-3 rb:[@media(max-width:600px)]:grid-cols-1">
                    {detail.members.map((item, index) => (
                      <div key={item.id ?? index} onClick={() => showMember(item)} className="rb:min-w-0 rb:p-3.5 rb:border rb:border-gray-400 rb:rounded-lg rb:bg-white rb:cursor-pointer rb:hover:border-gray-900">
                        <strong className="rb:block rb:mb-1.5 rb:text-[13px] rb:font-semibold">{item.name}</strong>
                        <span className="rb:block rb:text-gray-600 rb:text-[13px] rb:leading-5">{item.description || t('skills.noDescription')}</span>
                      </div>
                    ))}
                  </div>
                  : <Empty subTitle={t('skills.missingMembers')} size={88} />
                }
              </section>
            )}
            {legacy
              ? <Flex vertical gap={12}>
                <section aria-label={t('skills.mainfest')}>
                  <div>
                    <div className="rb:mb-2">{t('skills.keywords')}</div>
                    {current.config?.keywords?.length
                      ? <Flex wrap gap={4} className=".5">
                        {current.config.keywords.map((keyword, index) => <Tag className="rb:m-0!" key={`${keyword}-${index}`}>{keyword}</Tag>)}
                      </Flex>
                      : '-'
                    }
                  </div>
                </section>
                <div>
                  <div className="">{t('skills.promptConfiguration')}</div>
                  <div className="rb:bg-gray-100 rb:rounded-xl rb:p-2 rb:text-gray-600 rb:mt-2">{current.prompt || '-'}</div>
                </div>
                <ToolList key={current.id} value={current.tools} readOnly />
              </Flex>
              : (!plugin || member) &&
              <SkillFiles key={current.id} files={files} />
            }
          </Flex>
        )
        : null}
    </RbModal>
  );
});

export default SkillDetail;