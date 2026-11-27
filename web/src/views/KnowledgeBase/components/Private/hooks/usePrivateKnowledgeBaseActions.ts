import { useRef } from 'react';
import { App } from 'antd';
import copy from 'copy-to-clipboard';
import { useTranslation } from 'react-i18next';

import { batchDownloadFilesByKb, exportQaByKb } from '@/api/knowledgeBase';
import { getKnowledgeBaseDetailV1, patchKnowledgeBaseV1 } from '@/api/knowledgeBaseV1';
import type {
  MetadataDrawerRef,
  RecallTestDrawerRef,
  ShareModalRef,
} from '@/views/KnowledgeBase/types';
import type { V1KnowledgeListItem } from '@/views/KnowledgeBase/typesV1'
import type { V1KnowledgePatch } from '@/views/KnowledgeBase/typesV1';
import type { CreateKnowledgeBaseModalRef } from '@/views/KnowledgeBase/components/CreateKnowledgeBaseModal';

export const usePrivateKnowledgeBaseActions = (
  knowledgeBase: V1KnowledgeListItem | null,
  refreshKnowledgeBase: () => Promise<V1KnowledgeListItem | null>,
) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const createKbModalRef = useRef<CreateKnowledgeBaseModalRef>(null);
  const shareModalRef = useRef<ShareModalRef>(null);
  const recallTestDrawerRef = useRef<RecallTestDrawerRef>(null);
  const metadataDrawerRef = useRef<MetadataDrawerRef>(null);

  const changeKnowledgeBaseStatus = async (checked: boolean) => {
    if (!knowledgeBase) return;
    const updateData: V1KnowledgePatch = {
      status: checked ? 1 : 0,
    };
    await patchKnowledgeBaseV1(knowledgeBase.id, updateData);
    await refreshKnowledgeBase();
  };

  return {
    createKbModalRef,
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
    openKnowledgeBaseSettings: (type?: string) => {
      if (!knowledgeBase) return;
      // v1 edit: fetch the detail first to prefill; rebuild graph switches to the "Advanced Settings" section.
      getKnowledgeBaseDetailV1(knowledgeBase.id)
        .then((detail) => {
          createKbModalRef.current?.handleOpen(detail, type === 'rebuild' ? 'advanced' : undefined, undefined, type === 'rebuild');
        })
        .catch(() => undefined);
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
