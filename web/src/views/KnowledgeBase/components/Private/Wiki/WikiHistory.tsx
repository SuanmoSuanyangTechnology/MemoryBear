import { useState, type FC } from 'react';
import { HistoryOutlined } from '@ant-design/icons';
import { Button, List } from 'antd';
import { useTranslation } from 'react-i18next';
import {
  getWikiPageHistory,
  getWikiPageRevision,
  type WikiRevision,
  type WikiRevisionSummary,
} from '@/api/kbWiki';
import RbDrawer from '@/components/RbDrawer';
import RbMarkdown from '@/components/Markdown';

interface WikiHistoryProps {
  knowledgeBaseId: string;
  pageId: string;
}

const WikiHistory: FC<WikiHistoryProps> = ({ knowledgeBaseId, pageId }) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState<WikiRevisionSummary[]>([]);
  const [preview, setPreview] = useState<WikiRevision>();
  const [busy, setBusy] = useState(false);
  const [hasMore, setHasMore] = useState(false);

  const loadHistory = async (offset = 0) => {
    setBusy(true);
    try {
      const rows = await getWikiPageHistory(knowledgeBaseId, pageId, offset);
      setHistory((previous) => (offset ? [...previous, ...rows] : rows));
      setHasMore(rows.length === 50);
    } finally {
      setBusy(false);
    }
  };

  const previewVersion = async (version: number) => {
    setBusy(true);
    setPreview(undefined);
    try {
      setPreview(await getWikiPageRevision(knowledgeBaseId, pageId, version));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button
        type="text"
        icon={<HistoryOutlined />}
        onClick={() => {
          setOpen(true);
          setPreview(undefined);
          void loadHistory().catch(() => {});
        }}
      >
        {t('knowledgeBase.wikiHistory')}
      </Button>
      <RbDrawer
        title={t('knowledgeBase.wikiHistory')}
        open={open}
        width={480}
        onClose={() => setOpen(false)}
      >
        <List
          loading={busy}
          dataSource={history}
          locale={{ emptyText: t('knowledgeBase.wikiNoHistory') }}
          renderItem={(item) => (
            <List.Item
              actions={[
                <Button
                  key="preview"
                  disabled={busy}
                  onClick={() =>
                    void previewVersion(item.version).catch(() => {})
                  }
                >
                  {t('knowledgeBase.wikiPreview')}
                </Button>,
              ]}
            >
              v{item.version} · {new Date(item.created_at).toLocaleString()}
            </List.Item>
          )}
        />
        {hasMore && (
          <Button
            loading={busy}
            onClick={() => void loadHistory(history.length).catch(() => {})}
          >
            {t('knowledgeBase.wikiLoadMore')}
          </Button>
        )}
        {preview && (
          <>
            <h3>v{preview.version}</h3>
            <RbMarkdown
              content={preview.markdown}
              isNeedCopy={false}
            />
          </>
        )}
      </RbDrawer>
    </>
  );
};

export default WikiHistory;
