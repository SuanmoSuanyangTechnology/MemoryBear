import { forwardRef, useImperativeHandle, useRef, useState } from 'react';
import { App, Button, Flex, Spin } from 'antd';
import { EyeOutlined, UserOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import RbModal from '@/components/RbModal';
import { getAppAssetDetail } from '@/api/resourceCenter';
import type { AppAssetDetail } from '../types';
import WorkflowPreview from './WorkflowPreview';
import ClusterPreview from './ClusterPreview';
import Tag from '@/components/Tag';
import Empty from '@/components/Empty';
import ToolIcon from '@/views/ToolManagement/components/ToolIcon'
import SkillIcon from '@/views/Skills/components/SkillIcon';
import { typeIcons } from '@/views/ApplicationManagement/constants';
import RbMarkdown from '@/components/Markdown';

export interface ApplicationDetailModalRef {
  handleOpen: (id: string) => void;
}

interface ApplicationDetailModalProps {
  onAdd?: (id: string) => void;
}

const ApplicationDetailModal = forwardRef<ApplicationDetailModalRef, ApplicationDetailModalProps>(({
  onAdd
}, ref) => {
    const { t } = useTranslation();
    const { message } = App.useApp();
    const [open, setOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [item, setItem] = useState<AppAssetDetail>();
    const request = useRef(0);
    const close = () => {
      request.current += 1;
      setOpen(false);
    };
    useImperativeHandle(ref, () => ({
      handleOpen(id) {
        const token = ++request.current;
        setOpen(true);
        setItem(undefined);
        setLoading(true);
        getAppAssetDetail(id)
          .then((detail) => {
            if (token !== request.current) return;
            setItem(detail);
          })
          .catch(() => {
            if (token === request.current) message.error(t('resourceCenter.failed'));
          })
          .finally(() => {
            if (token === request.current) setLoading(false);
          });
      },
    }));
    const type = item?.source_app_type;
    return (
      <RbModal
        open={open}
        width={1120}
        title={
          item ? (
            <Flex vertical gap={8} className="rb:pr-6! rb:pl-2!">
              <Flex align="start" gap={16}>
                <div className={clsx("rb:rounded-2xl rb:bg-primary-600 rb:p-3 rb:shrink-0", {
                  'rb:bg-[#155EEF]': !(item.icon || typeIcons[item.source_app_type as string]) && item.source_app_type === 'agent',
                  'rb:bg-[#9C6FFF]!': !(item.icon || typeIcons[item.source_app_type as string]) && item.source_app_type === 'multi_agent',
                  'rb:bg-primary-600': !(item.icon || typeIcons[item.source_app_type as string]) && item.source_app_type?.includes('workflow'),
                  'rb:bg-transparent!': (item.icon || typeIcons[item.source_app_type as string])
                })}>
                  {(item.icon || typeIcons[item.source_app_type as string]) ? (
                    <img src={(item.icon || typeIcons[item.source_app_type as string])} alt="" className="rb:size-8" />
                  ) : (
                    <div className="rb:size-8 rb:flex rb:items-center rb:justify-center rb:text-white rb:text-[24px]">
                      {item.source_app_name?.trim()[0]}
                    </div>
                  )}
                </div>
                <div className="rb:flex-1 rb:min-w-0">
                  <Flex align="center" gap={8} wrap="wrap">
                    <h2 className="rb:text-xl rb:font-semibold rb:leading-7">{item.source_app_name}</h2>
                    <Tag color="processing">{item.published_version_name || item.published_version}</Tag>
                  </Flex>
                  <p className="rb:mt-2 rb:text-xs rb:text-gray-600 rb:font-normal!">
                    <EyeOutlined className="rb:mr-1" />
                    {item.view_count?.toLocaleString()} {t('resourceCenter.views')}
                    <span className="rb:mx-2">·</span>
                    <UserOutlined className="rb:mr-1" />
                    {item.publisher_name}
                  </p>
                </div>
                <Button type="primary" onClick={() => onAdd?.(item.id)}>
                  + {t('resourceCenter.add')}
                </Button>
              </Flex>
              <p className="rb:mb-2 rb:text-[14px] rb:font-normal!">{item.description || t("resourceCenter.noDescription")}</p>
              {item.categories.length > 0 &&
                <Flex align="center" wrap gap={8}>
                  {item.categories.map(category => <Tag key={category} color="processing">{category}</Tag>)}
                </Flex>
              }
            </Flex>
          ) : (
            <span>{t('resourceCenter.detail')}</span>
          )
        }
        onCancel={close}
        footer={null}
      >
        <Spin spinning={loading}>
          {item && (
            <>
              <div className="rb:grid rb:gap-6 rb:border-t rb:border-gray-200 rb:pt-3 rb:grid-cols-[minmax(0,1fr)_240px]">
                <section className="rb:min-w-0">
                  {type === 'workflow' &&
                    <>
                      <WorkflowPreview payload={item.display_payload.workflow} />
                      <section className="rb:mt-4">
                        <h3 className="rb:font-semibold">{t('application.setupSteps')}</h3>
                        {item.setup_steps
                          ? <RbMarkdown content={item.setup_steps} className="rb:mt-2 rb:text-gray-600" />
                          : <div className="rb:text-gray-600 rb:mt-2">{t('application.noSetupSteps')}</div>
                        }
                      </section>
                    </>
                  }
                  {type === 'multi_agent' && (
                    <ClusterPreview payload={item.display_payload?.cluster} name={item.source_app_name as string} />
                  )}
                  {type === 'agent' && item.usage_examples.length > 0 && (
                    <section>
                      <h3 className="rb:font-semibold">{t('resourceCenter.examples')}</h3>
                      {item.usage_examples.map((example, index) => (
                        <div key={index} className="rb:mt-3 rb:rounded-lg rb:border rb:border-gray-200 rb:bg-gray-100 rb:p-4">
                          <span className="rb:mr-3 rb:text-gray-600">{String(index + 1).padStart(2, '0')}</span>{example}
                        </div>
                      ))}
                    </section>
                  )}
                </section>
                <Flex vertical gap={24} className="rb-border-l rb:pl-6!">
                  <div>
                    <p className="rb:mb-2 rb:text-gray-600 rb:text-[12px]">{t('resourceCenter.type')}</p>
                    {type && <Tag>{t(`resourceCenter.${type}`)}</Tag>}
                  </div>
                  <div>
                    <p className="rb:mb-2 rb:text-gray-600 rb:text-[12px]">{t('resourceCenter.editPermission')}</p>
                    <Tag color={item.share_mode === 'editable' ? 'processing' : 'default'}>{t(`resourceCenter.${item.share_mode === 'editable' ? 'enabled' : 'disabled'}`)}</Tag>
                  </div>
                  <Flex vertical gap={8}>
                    <p className="rb:text-gray-600 rb:text-[12px]">{t('resourceCenter.relatedSkills')}</p>
                    {item.linked_skills.length
                      ? item.linked_skills.map(skill => (
                        <Flex align="center" gap={8} wrap key={skill.name}>
                          <SkillIcon plugin={skill.skill_type === 'plugin'} />
                          {skill.name}
                        </Flex>
                        ))
                      : <Empty size={88} />
                    }
                  </Flex>
                  <Flex vertical gap={8}>
                    <p className="rb:text-gray-600 rb:text-[12px]">{t('resourceCenter.relatedTools')}</p>
                    {item.linked_tools.length
                      ? item.linked_tools.map(tool => (
                        <Flex align="center" gap={8} wrap key={tool.name}>
                          <ToolIcon />
                          {tool.name}
                          <Tag>{tool.tool_type}</Tag>
                        </Flex>
                      ))
                      : <Empty size={88} />
                    }
                  </Flex>
                  <Flex vertical gap={8}>
                    <p className="rb:text-gray-600 rb:text-[12px]">{t('resourceCenter.updatedAt')}</p>
                    {item.updated_at ? new Date(item.updated_at).toLocaleString() : '—'}
                  </Flex>
                  <Flex vertical gap={8}>
                    <p className="rb:text-gray-600 rb:text-[12px]">{t('resourceCenter.updateNotes')}</p>
                    {item.changelog || t('resourceCenter.noUpdateNotes')}
                  </Flex>
                </Flex>
              </div>
            </>
          )}
        </Spin>
      </RbModal>
    );
  }
);

export default ApplicationDetailModal;
