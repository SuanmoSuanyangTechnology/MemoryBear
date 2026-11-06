import { useRef, forwardRef, useImperativeHandle, type ReactNode } from 'react';
import { Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ToolItem, CustomToolModalRef, CustomRef } from './types';
import { useToolList } from './hooks/useToolList';
import { useToolDelete } from './hooks/useToolDelete';
import CustomToolModal from './components/CustomToolModal';
import CustomToolDetailModal, { type CustomToolDetailModalRef } from './components/CustomToolDetailModal';
import ToolCard from './components/ToolCard';
import BodyWrapper from '@/components/Empty/BodyWrapper';
import MoreDropdown from '@/components/MoreDropdown';
import OverflowTags from '@/components/OverflowTags';
import Tag from '@/components/Tag';
import { Row, Col } from 'antd';

const Custom = forwardRef<CustomRef, { getStatusTag: (status: string) => ReactNode; keyword?: string | undefined }>(
  ({ getStatusTag, keyword }, ref) => {
    const { t } = useTranslation();
  const { loading, data, getData } = useToolList<ToolItem>({
    toolType: 'custom',
    keyword,
  });
  const handleDelete = useToolDelete(getData);
    const customToolModalRef = useRef<CustomToolModalRef>(null);
    const detailModalRef = useRef<CustomToolDetailModalRef>(null);

    const handleEdit = (data?: ToolItem) => {
      customToolModalRef.current?.handleOpen(data);
    };

    useImperativeHandle(ref, () => ({ handleEdit }));

    return (
      <>
        <BodyWrapper loading={loading} empty={data.length === 0}>
          <Row gutter={[16, 16]}>
            {data.map((item) => (
              <Col span={8} key={item.id}>
                <ToolCard
                  item={item}
                  onClick={() => detailModalRef.current?.handleOpen(item)}
                  statusTag={getStatusTag(item.status)}
                  description={item.description}
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
                          className: 'rb:text-[#FF5D34]!',
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

        <CustomToolModal ref={customToolModalRef} refresh={getData} />
        <CustomToolDetailModal ref={detailModalRef} onEdit={handleEdit} onDelete={handleDelete} getStatusTag={getStatusTag} />
      </>
    );
  },
);

Custom.displayName = 'Custom';

export default Custom;
