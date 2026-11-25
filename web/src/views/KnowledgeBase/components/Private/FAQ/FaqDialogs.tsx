import { useState } from 'react';
import { App, Button, Drawer, Input, Modal, Space, Tag, Typography, Upload } from 'antd';
import { useTranslation } from 'react-i18next';
import type { FaqItem } from '@/api/knowledgeBase';

interface FaqDialogsProps {
  detail?: FaqItem;
  importOpen: boolean;
  busy: boolean;
  onCloseDetail: () => void;
  onCloseImport: () => void;
  onEdit: (item: FaqItem) => void;
  onImport: (records: string[][]) => Promise<void>;
}

export default function FaqDialogs({
  detail,
  importOpen,
  busy,
  onCloseDetail,
  onCloseImport,
  onEdit,
  onImport,
}: FaqDialogsProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [importText, setImportText] = useState('');

  const handleImport = async () => {
    const records: string[][] = [];
    let row: string[] = [];
    let cell = '';
    let quoted = false;
    const text = importText.replace(/^\uFEFF/, '');

    for (let i = 0; i < text.length; i++) {
      const char = text[i];
      if (char === '"') {
        if (quoted && text[i + 1] === '"') {
          cell += '"';
          i++;
        } else {
          quoted = !quoted;
        }
      } else if (!quoted && (char === ',' || char === '\n')) {
        row.push(cell);
        cell = '';
        if (char === '\n') {
          records.push(row);
          row = [];
        }
      } else if (char !== '\r' || quoted) {
        cell += char;
      }
    }
    if (quoted) {
      message.warning(t('knowledgeBase.faq.csvQuotes'));
      return;
    }
    row.push(cell);
    records.push(row);
    const values = records.filter((record) => record.some((value) => value.trim()));
    const header = values[0]?.map((value) => value.trim().toLowerCase());
    if ((header?.[0] === '问题' && header[1] === '答案')
      || (header?.[0] === 'question' && header[1] === 'answer')) {
      values.shift();
    }
    if (!values.length || values.some((record) => record.length !== 2 || !record[0].trim() || !record[1].trim())) {
      message.warning(t('knowledgeBase.faq.csvInvalid'));
      return;
    }
    await onImport(values);
  };

  return (
    <>
      <Modal
        title={t('knowledgeBase.faq.importTitle')}
        open={importOpen}
        confirmLoading={busy}
        onCancel={onCloseImport}
        onOk={handleImport}
        afterClose={() => setImportText('')}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Typography.Paragraph>{t('knowledgeBase.faq.importHelp')}</Typography.Paragraph>
          <Upload
            accept=".csv,text/csv"
            showUploadList={false}
            beforeUpload={async (file) => {
              try {
                setImportText(await file.text());
              } catch {
                message.error(t('knowledgeBase.faq.readFailed'));
              }
              return false;
            }}
          >
            <Button>{t('knowledgeBase.faq.chooseFile')}</Button>
          </Upload>
          <Input.TextArea
            rows={8}
            value={importText}
            onChange={(event) => setImportText(event.target.value)}
            placeholder={t('knowledgeBase.faq.csvHeader')}
          />
        </Space>
      </Modal>
      <Drawer title={t('knowledgeBase.faq.detail')} open={!!detail} onClose={onCloseDetail}>
        {detail && (
          <Space direction="vertical" size="large">
            <Tag>{t(`knowledgeBase.faq.${detail.status}`)}</Tag>
            <Typography.Title level={3}>{detail.question}</Typography.Title>
            <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{detail.answer}</Typography.Paragraph>
            <Button onClick={() => onEdit(detail)}>{t('knowledgeBase.faq.edit')}</Button>
          </Space>
        )}
      </Drawer>
    </>
  );
}
