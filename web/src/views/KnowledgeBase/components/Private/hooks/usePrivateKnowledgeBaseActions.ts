import { useRef } from 'react';
import { App } from 'antd';
import copy from 'copy-to-clipboard';
import { useTranslation } from 'react-i18next';

import { batchDownloadFilesByKb, exportQaByKb, updateKnowledgeBase } from '@/api/knowledgeBase';
import type {
  CreateModalRef,
  KnowledgeBaseFormData,
  KnowledgeBaseListItem,
  MetadataDrawerRef,
  RecallTestDrawerRef,
  ShareModalRef,
} from '@/views/KnowledgeBase/types';

const DEFAULT_PARSER_CONFIG = {
  chunk_token_num: 512,
  delimiter: '\n',
  auto_keywords: 0,
  auto_questions: 0,
  html4excel: false,
  graphrag: {
    use_graphrag: false,
    scene_name: '',
    entity_types: [],
    method: '',
    resolution: false,
    community: false,
  },
};

export const usePrivateKnowledgeBaseActions = (
  knowledgeBase: KnowledgeBaseListItem | null,
  refreshKnowledgeBase: () => Promise<KnowledgeBaseListItem | null>,
) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const createModalRef = useRef<CreateModalRef>(null);
  const shareModalRef = useRef<ShareModalRef>(null);
  const recallTestDrawerRef = useRef<RecallTestDrawerRef>(null);
  const metadataDrawerRef = useRef<MetadataDrawerRef>(null);

  const changeKnowledgeBaseStatus = async (checked: boolean) => {
    if (!knowledgeBase) return;
    const updateData: KnowledgeBaseFormData = {
      name: knowledgeBase.name,
      description: knowledgeBase.description,
      embedding_id: knowledgeBase.embedding_id,
      llm_id: knowledgeBase.llm_id,
      image2text_id: knowledgeBase.image2text_id,
      reranker_id: knowledgeBase.reranker_id,
      audio2text_id: knowledgeBase.audio2text_id,
      video2text_id: knowledgeBase.video2text_id,
      permission_id: knowledgeBase.permission_id,
      type: knowledgeBase.type,
      status: checked ? 1 : 0,
      parser_config: knowledgeBase.parser_config || DEFAULT_PARSER_CONFIG,
    };
    await updateKnowledgeBase(knowledgeBase.id, updateData);
    await refreshKnowledgeBase();
  };

  return {
    createModalRef,
    shareModalRef,
    recallTestDrawerRef,
    metadataDrawerRef,
    copyKnowledgeBaseId: () => {
      if (!knowledgeBase) return;
      copy(knowledgeBase.id);
      message.success(t('common.copySuccess'));
    },
    changeKnowledgeBaseStatus,
    shareKnowledgeBase: () => {
      if (knowledgeBase) shareModalRef.current?.handleOpen(knowledgeBase.id, knowledgeBase);
    },
    openRecallTest: () => {
      if (knowledgeBase) recallTestDrawerRef.current?.handleOpen(knowledgeBase.id);
    },
    openMetadata: () => {
      if (knowledgeBase) metadataDrawerRef.current?.handleOpen(knowledgeBase.id);
    },
    openKnowledgeBaseSettings: (step?: number) => {
      if (knowledgeBase) createModalRef.current?.handleOpen(knowledgeBase, '', step);
    },
    batchDownloadKnowledgeBase: () => {
      if (!knowledgeBase) return;
      return batchDownloadFilesByKb(
        knowledgeBase.id,
        `${knowledgeBase.name}.zip`,
        () => message.success(t('knowledgeBase.batchDownloadSuccess')),
      );
    },
    exportKnowledgeBaseQa: () => {
      if (!knowledgeBase) return;
      return exportQaByKb(
        knowledgeBase.id,
        `${knowledgeBase.name}.csv`,
        () => message.success(t('knowledgeBase.exportQaSuccess')),
      );
    },
  };
};
