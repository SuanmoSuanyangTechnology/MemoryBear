import { useRef, type ReactNode } from 'react';
import { Flex, Button } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ToolItem, TimeToolModalRef, JsonToolModalRef, InnerToolModalRef, DatabaseToolModalRef } from './types';
import { useToolList } from './hooks/useToolList';
import ToolCard from './components/ToolCard';
import BodyWrapper from '@/components/Empty/BodyWrapper';
import TimeToolModal from './components/TimeToolModal';
import JsonToolModal from './components/JsonToolModal';
import InnerToolModal from './components/InnerToolModal';
import DatabaseToolModal from './components/DatabaseToolModal';
import { InnerConfigData } from './constant';
import OverflowTags from '@/components/OverflowTags';
import Tag from '@/components/Tag';
import { Row, Col } from 'antd';

const Inner: React.FC<{ getStatusTag: (status: string) => ReactNode; keyword?: string | undefined }> = ({
  getStatusTag,
  keyword,
}) => {
  const { t } = useTranslation();
  const { loading, data, getData } = useToolList<ToolItem>({
    toolType: 'builtin',
    keyword,
  });
  const timeToolModalRef = useRef<TimeToolModalRef>(null);
  const jsonToolModalRef = useRef<JsonToolModalRef>(null);
  const innerToolModalRef = useRef<InnerToolModalRef>(null);
  const databaseToolModalRef = useRef<DatabaseToolModalRef>(null);

  const handleEdit = (data: ToolItem) => {
    switch (data.config_data.tool_class) {
      case 'DateTimeTool':
        timeToolModalRef.current?.handleOpen(data);
        break;
      case 'JsonTool':
        jsonToolModalRef.current?.handleOpen(data);
        break;
      case 'DatabaseTool':
        databaseToolModalRef.current?.handleOpen(data);
        break;
      default:
        innerToolModalRef.current?.handleOpen(data);
        break;
    }
  };

  return (
    <>
      <BodyWrapper loading={loading} empty={data.length === 0}>
        <Row gutter={[16, 16]}>
          {data.map((item) => (
            <Col span={8} key={item.id}>
              <ToolCard
                item={item}
                statusTag={getStatusTag(item.status)}
                description={t(`tool.${item.config_data.tool_class}_features`)}
              >
                <Flex align="center" justify="space-between" gap={8}>
                  <OverflowTags
                    items={InnerConfigData[item.config_data.tool_class].features?.map((type, i) => (
                      <Tag key={i} variant="borderless" color="dark">
                        {t(`tool.${type}`)}
                      </Tag>
                    ))}
                    numTag={(num?: number) => (
                      <Tag variant="borderless" color="dark">
                        +{num}
                      </Tag>
                    )}
                  />
                  <Button
                    icon={
                      <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/edit.svg')] rb:group-hover/btn:bg-[url('@/assets/images/common/edit_blue.svg')]" />
                    }
                    className="rb:h-6! rb:py-0! rb:px-2! rb:rounded-md! rb:text-[12px]! rb:group/btn"
                    onClick={() => handleEdit(item)}
                  >
                    {t('common.edit')}
                  </Button>
                </Flex>
              </ToolCard>
            </Col>
          ))}
        </Row>
      </BodyWrapper>

      <TimeToolModal ref={timeToolModalRef} />
      <JsonToolModal ref={jsonToolModalRef} />
      <InnerToolModal ref={innerToolModalRef} refreshTable={getData} />
      <DatabaseToolModal ref={databaseToolModalRef} />
    </>
  );
};

export default Inner;
