import { forwardRef, useImperativeHandle, useState } from 'react';
import { Flex, Row, Col } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import type { Data } from '../types';
import RbModal from '@/components/RbModal';
import Tag from '@/components/Tag';
import { formatDateTime } from '@/utils/format'

export interface MemorySubjectDetailModalRef {
  handleOpen: (item: Data) => void;
  handleClose: () => void;
}

interface DetailFieldProps {
  label: string;
  value?: string | number;
  className?: string;
}

const DetailField = ({ label, value, className = '' }: DetailFieldProps) => (
  <div className={className}>
    <div className="rb:text-[#5B6167] rb:leading-5">{label}</div>
    <div className="rb:mt-1 rb:font-medium rb:leading-6 rb:break-all">
      {value ?? '-'}
    </div>
  </div>
);

const MemorySubjectDetailModal = forwardRef<MemorySubjectDetailModalRef>((_, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [item, setItem] = useState<Data | null>(null);

  const handleClose = () => {
    setVisible(false);
    setItem(null);
  };

  const handleOpen = (currentItem: Data) => {
    setItem(currentItem);
    setVisible(true);
  };

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose,
  }));

  const memoryType = item?.end_user?.label
    ? t(`userMemory.${item.end_user.label}TermMemory`)
    : '-';
  const tags = item?.tags || [];

  return (
    <RbModal
      title={t('userMemory.memorySubject')}
      open={visible}
      width={800}
      onCancel={handleClose}
    >
      <section>
        <h3 className="rb:mb-4 rb:font-semibold rb:leading-6">
          {t('userMemory.basicInfo')}
        </h3>

        <DetailField
          label={t('userMemory.subjectId')}
          value={item?.end_user_id}
        />


        <Row gutter={24} className="rb:my-3">
          <Col span={12}>
            <div className="rb:text-[#5B6167] rb:leading-5">
              {t('userMemory.userName')}
            </div>
            <div className={clsx("rb:mt-1 rb:leading-6", {
              'rb:text-[#5B6167]': !item?.end_user?.other_name,
              'rb:font-medium': !!item?.end_user?.other_name
            })}>
              {item?.end_user?.other_name || t('userMemory.unnamed')}
            </div>
          </Col>
          <Col span={12}>
            <div className="rb:text-[#5B6167] rb:leading-5">
              {t('userMemory.memoryType')}
            </div>
            {item?.end_user?.label && <Tag color={item?.end_user?.label === 'long' ? "processing" : 'warning'} className="rb:mt-1!">{memoryType}</Tag>}
          </Col>
        </Row>

        <div className="rb:mb-3 rb:rounded-xl rb:bg-[rgba(21,94,239,0.08)] rb:p-3">
          <div className="rb:text-[#5B6167] rb:leading-5">
            {t('userMemory.tags')}
          </div>
          {tags.length > 0 ? (
            <Flex gap={8} wrap="wrap" className="rb:mt-2!">
              {tags.map(tag => <Tag key={tag} color="default">{tag}</Tag>)}
            </Flex>
          ) : (
            <div className="rb:mt-1 rb:text-[#A4A9AE] rb:leading-6">
              {t('userMemory.noTags')}
            </div>
          )}
        </div>
        <div className="rb:mb-3 rb:rounded-xl rb:bg-[rgba(21,94,239,0.08)] rb:p-3">
          <DetailField
            label={t('userMemory.identity')}
            value={item?.end_user?.other_id || t('userMemory.unboundIdentity')}
          />
        </div>

        <Row gutter={24}>
          <Col span={12}>
            <DetailField
              label={t('userMemory.lastMemoryActivityTime')}
              value={item?.end_user?.write_time ? formatDateTime(item.end_user.write_time) : '-'}
            />
          </Col>
          <Col span={12}>
            <DetailField
              label={t('userMemory.expireTime')}
              value={item?.end_user?.expire_time ? formatDateTime(item.end_user.expire_time) : t('userMemory.neverExpires')}
            />
          </Col>
        </Row>
      </section>

      <section className="rb:mt-3 rb:border-t rb:border-[#EBEBEB] rb:pt-3">
        <h3 className="rb:mb-4 rb:font-semibold rb:leading-6">
          {t('userMemory.memoryStatistics')}
        </h3>
        <div className="rb:grid rb:grid-cols-3 rb:gap-3">
          {[
            [t('userMemory.totalNumOfMemories'), item?.memory_num?.total ?? 0],
            [t('userMemory.activeMemoryCount'), item?.memory_num?.active_count ?? 0],
            [t('userMemory.capacityLimit'), item?.memory_num?.memory_limit ?? 0],
          ].map(([label, value]) => (
            <div key={String(label)} className="rb:rounded-xl rb:border rb:border-[#EBEBEB] rb:p-3">
              <div className="rb:text-[#5B6167] rb:leading-5">{label}</div>
              <div className="rb:mt-1 rb:text-[20px] rb:font-semibold rb:leading-9">{value}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="rb:mt-3 rb:border-t rb:border-[#EBEBEB] rb:pt-3">
        <h3 className="rb:mb-4 rb:font-semibold rb:leading-6">
          {t('userMemory.memoryConfiguration')}
        </h3>
        <div className="rb:grid rb:grid-cols-2 rb:gap-6">
          <DetailField
            label={t('userMemory.configurationName')}
            value={item?.memory_config?.memory_config_name}
          />
          <DetailField
            label={t('userMemory.configurationId')}
            value={item?.memory_config?.memory_config_id}
          />
        </div>
      </section>
    </RbModal>
  );
});

export default MemorySubjectDetailModal;
