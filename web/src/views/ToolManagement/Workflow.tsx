import { useRef, type ReactNode } from 'react';
import { Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import type { WorkflowToolItem, WorkflowToolModalRef } from './types';
import type { WorkflowDetailModalRef } from './components/WorkflowDetailModal';
import { useToolList } from './hooks/useToolList';
import { useToolDelete } from './hooks/useToolDelete';
import PublishAsToolModal from './components/PublishAsToolModal';
import WorkflowDetailModal from './components/WorkflowDetailModal';
import ToolCard from './components/ToolCard';
import BodyWrapper from '@/components/Empty/BodyWrapper';
import MoreDropdown from '@/components/MoreDropdown';
import OverflowTags from '@/components/OverflowTags';
import Tag from '@/components/Tag';
import { Row, Col } from 'antd';

const Workflow: React.FC<{ getStatusTag: (status: string) => ReactNode; keyword?: string }> = ({
  getStatusTag,
  keyword,
}) => {
  const { t } = useTranslation();
  const { loading, data, getData } = useToolList<WorkflowToolItem>({
    toolType: 'workflow',
    keyword,
  });
  const handleDelete = useToolDelete(getData);
  const publishAsToolModalRef = useRef<WorkflowToolModalRef>(null);
  const workflowDetailModalRef = useRef<WorkflowDetailModalRef>(null);

  const handleEdit = (item: WorkflowToolItem) => {
    publishAsToolModalRef.current?.handleOpen(item);
  };

  return (
    <>
      <BodyWrapper loading={loading} empty={data.length === 0}>
        <Row gutter={[16, 16]}>
          {data.map((item) => (
            <Col key={item.id} span={8}>
              <ToolCard
                item={item}
                statusTag={getStatusTag(item.status)}
                description={item.description}
                onClick={() => workflowDetailModalRef.current?.handleOpen(item)}
              >
                <Flex align="center" justify="space-between" gap={8} className="rb:mt-3!">
                  {item.tags?.length > 0 ? (
                    <OverflowTags
                      items={item.tags?.map((type, i) => (
                        <Tag variant="borderless" color="dark" key={i}>
                          {type}
                        </Tag>
                      ))}
                      numTag={(num?: number) => (
                        <Tag variant="borderless" color="dark">
                          +{num}
                        </Tag>
                      )}
                    />
                  ) : (
                    <div className="rb:text-gray-500 rb:leading-5 rb:text-[12px]">
                      {t('tool.noTags')}
                    </div>
                  )}
                  <MoreDropdown
                    items={[
                      {
                        key: 'edit',
                        icon: (
                          <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/edit_bold.svg')]" />
                        ),
                        label: t('common.edit'),
                        onClick: () => handleEdit(item),
                      },
                      {
                        key: 'delete',
                        danger: true,
                        icon: (
                          <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/delete_red_big.svg')]" />
                        ),
                        label: t('common.delete'),
                        onClick: () => handleDelete(item),
                      },
                    ]}
                  />
                </Flex>
              </ToolCard>
            </Col>
          ))}
        </Row>
      </BodyWrapper>
      <PublishAsToolModal ref={publishAsToolModalRef} refresh={getData} />
      <WorkflowDetailModal
        ref={workflowDetailModalRef}
        onEdit={handleEdit}
        onDelete={handleDelete}
        getStatusTag={getStatusTag}
      />
    </>
  );
};

export default Workflow;
