import type { FC, MutableRefObject } from 'react';

import type {
  CreateDatasetModalRef,
  CreateFolderModalRef,
  CreateSetModalRef,
} from '@/views/KnowledgeBase/types';
import CreateContentModal from '../CreateContentModal';
import CreateDatasetModal from '../CreateDatasetModal';
import CreateFolderModal from '../CreateFolderModal';
import CreateImageDataset from '../CreateImageDataset';

interface PrivateKnowledgeBaseDialogsProps {
  createContentModalRef: MutableRefObject<CreateSetModalRef | null>;
  createFolderModalRef: MutableRefObject<CreateFolderModalRef | null>;
  createImageDatasetRef: MutableRefObject<CreateSetModalRef | null>;
  datasetModalRef: MutableRefObject<CreateDatasetModalRef | null>;
  onRefreshDirectory: () => Promise<void>;
  onRefreshTable: () => Promise<void>;
}

const PrivateKnowledgeBaseDialogs: FC<PrivateKnowledgeBaseDialogsProps> = ({
  createContentModalRef,
  createFolderModalRef,
  createImageDatasetRef,
  datasetModalRef,
  onRefreshDirectory,
  onRefreshTable,
}) => (
  <>
    <CreateFolderModal ref={createFolderModalRef} refreshTable={onRefreshDirectory} />
    <CreateContentModal ref={createContentModalRef} refreshTable={onRefreshTable} />
    <CreateDatasetModal ref={datasetModalRef} handleCreateDataset={() => undefined} />
    <CreateImageDataset ref={createImageDatasetRef} refreshTable={onRefreshDirectory} />
  </>
);

export default PrivateKnowledgeBaseDialogs;
