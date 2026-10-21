import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { App, Form, Input, Button, Space, Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import { updateFaq, getFaqDetail } from '@/api/kbWiki';
import type { FaqItem, FaqForm } from './types'
import RbDrawer from '@/components/RbDrawer';
import StatusTag from '@/components/StatusTag';
import { sourceStatusColors, statusColors } from './constants';
import { formatDateTime } from '@/utils/format'
import Tag from '@/components/Tag';

export interface FaqDetailDrawerRef {
  handleOpen: (item?: FaqItem, editMode?: boolean, onClose?: () => void) => void;
}

interface FaqDetailDrawerProps {
  knowledgeBaseId: string;
  refresh: () => void;
  onOpenWikiPage: (pageId: string) => void;
}

const FaqDetailDrawer = forwardRef<FaqDetailDrawerRef, FaqDetailDrawerProps>(({
  knowledgeBaseId, refresh, onOpenWikiPage
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [editing, setEditing] = useState<FaqItem | undefined>(undefined);
  const [form] = Form.useForm<FaqForm>();
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);

  const [detail, setDetail] = useState<FaqItem>()
  const [isEdit, setIsEdit] = useState(false);
  const [isFromEdit, setIsFromEdit] = useState(false)
  const onCloseRef = useRef<(() => void) | undefined>(undefined);
  const handleClose = () => {
    setVisible(false);
    setEditing(undefined)
    form.resetFields()
    setIsEdit(false)
    setDetail(undefined)
    refresh?.()
    setIsFromEdit(false)
    const onClose = onCloseRef.current;
    onCloseRef.current = undefined;
    onClose?.();
  };

  const handleSave = () => {
    if (!editing) return

    form.validateFields()
      .then((values) => {
        setLoading(true)
        updateFaq(knowledgeBaseId, editing.id, {
          ...values,
          expected_row_version: editing.row_version
        })
          .then(() => {
            message.success(t('common.updateSuccess'))
            if (isFromEdit) {
              handleClose()
            }
          })
          .finally(() => {
            setLoading(false)
          })
      })
  };

  const getDetail = useCallback(() => {
    if (!editing) return
    getFaqDetail(knowledgeBaseId, editing.id)
      .then(res => {
        setDetail(res)
      })
  }, [editing, knowledgeBaseId])

  useEffect(() => {
    getDetail()
  }, [editing?.id, getDetail])

  const handleEdit = () => {
    if (!detail) return
    setIsEdit(true)
    form.setFieldsValue({
      standard_question: detail.standard_question,
      standard_answer: detail.standard_answer,
      expected_row_version: detail.row_version
    })
  }
  const handleCancel = () => {
    if (isFromEdit) {
      handleClose()
    }
    setIsEdit(false)
    form.resetFields()
  }

  useImperativeHandle(ref, () => ({
    handleOpen: (item, editMode = false, onClose) => {
      onCloseRef.current = onClose;
      setIsEdit(editMode);
      setIsFromEdit(editMode)
      setVisible(true);
      if (item) {
        form.setFieldsValue(item)
        setEditing(item);
      }
    },
  }));

  return (
    <RbDrawer
      title={t(isEdit ? 'knowledgeBase.faq.editTitle' : 'knowledgeBase.faq.detail')}
      open={visible}
      width={480}
      onClose={handleClose}
      footer={isEdit
        ? (
          <Space size={12}>
            <Button loading={loading} onClick={handleCancel}>{t('common.cancel')}</Button>
            <Button type="primary" loading={loading} onClick={handleSave}>{t('common.save')}</Button>
          </Space>
        )
        : <Button type="primary" onClick={handleEdit}>{t('knowledgeBase.faq.edit')}</Button>
      }
    >
      {isEdit
        ? (
          <Form
            form={form}
            key={editing?.id ?? 'new'}
            layout="vertical"
          >
            <Form.Item
              name="standard_question"
              label={t('knowledgeBase.faq.question')}
              rules={[{ required: true, whitespace: true, message: t('knowledgeBase.faq.questionRequired') }]}
              extra={t('knowledgeBase.faq.questionExample')}
            >
              <Input.TextArea rows={2} maxLength={200} showCount />
            </Form.Item>
            <Form.Item
              name="standard_answer"
              label={t('knowledgeBase.faq.answer')}
              rules={[{ required: true, whitespace: true, message: t('knowledgeBase.faq.answerRequired') }]}
            >
              <Input.TextArea rows={7} />
            </Form.Item>
            <Form.Item name="expected_row_version" hidden />
          </Form>
        )
        : detail
        ? (
          <Flex vertical gap={12}>
            <Flex gap={12} align="center" className="rb:text-gray-600 rb:text-[12px] rb:mb-3!">
              <StatusTag
                status={statusColors[detail.review_status]}
                text={t(`knowledgeBase.faq.${detail.review_status}`)}
              />

              <span>{t(detail.is_enabled ? 'knowledgeBase.faq.enabledHint' : 'knowledgeBase.faq.disabledHint')}</span>

              <span>{t('knowledgeBase.faq.updatedAtTime', { time: formatDateTime(detail.updated_at_ms) })}</span>
            </Flex>

            <div className="rb:text-gray-600">{t('knowledgeBase.faq.question')}</div>
            <div className="rb:font-medium rb:text-[16px]">{detail.standard_question}</div>
            <div className="rb:text-gray-600 rb:mt-3">{t('knowledgeBase.faq.answer')}</div>
            <div>{detail.standard_answer}</div>


            <div className="rb:text-gray-600 rb:text-[12px] rb:mt-3!">{t('knowledgeBase.faq.sourceWiki')}</div>
            <ul className="rb:list-disc rb:pl-6">
              <li>
                <Button
                  type="link"
                  className="rb:h-auto! rb:p-0! rb:text-[12px]!"
                  disabled={!detail.source.wiki_page_id}
                  onClick={() => {
                    if (detail?.source?.wiki_page_id && detail?.source?.source_status === 'available') onOpenWikiPage(detail.source.wiki_page_id)
                  }}
                >
                  {detail.source.title || detail.source.wiki_page_id}
                </Button>
                <Tag color={sourceStatusColors[detail.source.source_status]} className="rb:ml-2">
                  {t(`knowledgeBase.faq.sourceStatus.${detail.source.source_status}`)}
                </Tag>
              </li>
            </ul>
          </Flex>
        )
        : null
      }
    </RbDrawer>
  );
});

export default FaqDetailDrawer;
