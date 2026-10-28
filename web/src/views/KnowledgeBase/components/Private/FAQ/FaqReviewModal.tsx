import { forwardRef, useImperativeHandle, useRef, useState } from 'react';
import { Alert, App, Button, Spin, Typography, Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal';
import Tag from '@/components/Tag';
import { sourceStatusColors } from './constants';
import { approveFaq, getFaqReviewContext } from '@/api/kbWiki';
import FaqWikiSource from './FaqWikiSource';
import type { FaqItem, GetReviewFaqContext } from './types';

export interface FaqReviewModalRef {
  handleOpen: (editing: FaqItem) => void;
}

interface FaqReviewModalProps {
  knowledgeBaseId: string;
  refresh: () => void;
  onEdit: (editing: FaqItem) => void;
  onOpenWikiPage: (pageId: string) => void;
}

const FaqReviewModal = forwardRef<FaqReviewModalRef, FaqReviewModalProps>(({
  knowledgeBaseId, refresh, onEdit, onOpenWikiPage,
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [editing, setEditing] = useState<FaqItem>();
  const [detail, setDetail] = useState<GetReviewFaqContext>();
  const [fetching, setFetching] = useState(false);
  const [loading, setLoading] = useState(false);
  const requestId = useRef(0);
  const approving = useRef(false);
  const idempotencyKey = useRef('');
  const faq = detail?.faq ?? editing;

  const handleClose = () => {
    if (approving.current) return;
    requestId.current += 1;
    setVisible(false);
    setEditing(undefined);
    setDetail(undefined);
    setFetching(false);
  };

  const handleApprove = async () => {
    if (!detail || !detail?.can_approve || approving.current) return;
    approving.current = true;
    setLoading(true);
    try {
      await approveFaq(knowledgeBaseId, detail.faq.id, {
        expected_row_version: detail.faq.row_version,
        content_revision: detail.faq.content_revision,
        reviewed_wiki_snapshot_id: detail.reviewed_wiki_snapshot_id,
      });
      message.success(t('common.updateSuccess'));
      approving.current = false;
      handleClose();
      refresh();
    } catch {
      message.error(t('knowledgeBase.faq.operationFailed'));
    } finally {
      approving.current = false;
      setLoading(false);
    }
  };

  useImperativeHandle(ref, () => ({
    handleOpen: (item) => {
      if (approving.current) return;
      const currentRequest = ++requestId.current;
      idempotencyKey.current = crypto.randomUUID();
      setEditing(item);
      setDetail(undefined);
      setVisible(true);
      setFetching(true);
      getFaqReviewContext(knowledgeBaseId, item.id)
        .then((response) => {
          if (requestId.current === currentRequest) setDetail(response);
        })
        .catch(() => {
          if (requestId.current === currentRequest) {
            message.error(t('knowledgeBase.faq.operationFailed'));
          }
        })
        .finally(() => {
          if (requestId.current === currentRequest) setFetching(false);
        });
    },
  }));

  return (
    <RbModal
      title={t('knowledgeBase.faq.reviewChanges')}
      open={visible}
      width={1000}
      onCancel={handleClose}
      closable={!loading}
      keyboard={!loading}
      footer={[
        <Button
          key="edit"
          disabled={loading || fetching || !faq}
          onClick={() => {
            if (!faq) return;
            handleClose();
            onEdit(faq);
          }}
        >
          {t('knowledgeBase.faq.edit')}
        </Button>,
        <Button key="close" disabled={loading} onClick={handleClose}>
          {t('knowledgeBase.faq.closeReview')}
        </Button>,
        <Button
          key="submit"
          type="primary"
          loading={loading}
          disabled={fetching || !detail?.can_approve}
          onClick={handleApprove}
        >
          {t('knowledgeBase.faq.reviewApprove')}
        </Button>,
      ]}
    >
      <Spin spinning={fetching}>
        <div className="rb:bg-gray-100 rb:rounded-xl rb:p-3 rb:font-medium">
          <div className="rb:font-medium">{t('knowledgeBase.faq.question')}</div>
          <div className="rb:mt-2 rb:text-[16px]">
            {faq?.standard_question}
          </div>
        </div>
        <div className="rb:bg-gray-100 rb:rounded-xl rb:p-3 rb:mt-3">
          <div className="rb:font-medium">{t('knowledgeBase.faq.answer')}</div>
          <div className="rb:mt-2">
            {faq?.standard_answer}
          </div>
        </div>
        <Flex align="center" justify="space-between" className="rb:mt-3!">
          <Typography.Title level={5}>
            {t(detail?.before?.version === detail?.after?.version
              ? 'knowledgeBase.faq.relatedWiki'
              : 'knowledgeBase.faq.wikiSourceComparison')}
          </Typography.Title>

          <span>
            {t('knowledgeBase.faq.sourceWikiLabel')} <Button
              type="link"
              className="rb:h-auto! rb:p-0!"
              disabled={loading || !faq?.source?.wiki_page_id}
              onClick={() => {
                if (faq?.source?.wiki_page_id && faq?.source?.source_status === 'available') onOpenWikiPage(faq.source.wiki_page_id);
              }}
            >
              {faq?.source?.title || faq?.source?.wiki_page_id}
            </Button>
            {faq?.source && (
              <Tag color={sourceStatusColors[faq.source.source_status]} className="rb:ml-2">
                {t(`knowledgeBase.faq.sourceStatus.${faq.source.source_status}`)}
              </Tag>
            )}
          </span>
        </Flex>
        {!fetching && !detail && (
          <Alert type="warning" showIcon message={t('knowledgeBase.faq.reviewMissing')} />
        )}
        {detail && (
          <FaqWikiSource before={detail.before} after={detail.after} />
        )}
      </Spin>
    </RbModal>
  );
});

export default FaqReviewModal;
