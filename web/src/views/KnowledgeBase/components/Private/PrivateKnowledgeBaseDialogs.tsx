import type { FC, MutableRefObject } from 'react';

import type {
  CreateDatasetModalRef,
  CreateFolderModalRef,
} from '@/views/KnowledgeBase/types';
import CreateFolderModal from '../CreateFolderModal';

interface PrivateKnowledgeBaseDialogsProps {
  createFolderModalRef: MutableRefObject<CreateFolderModalRef | null>;
  datasetModalRef: MutableRefObject<CreateDatasetModalRef | null>;
  onRefreshDirectory: () => Promise<void>;
  onRefreshTable: () => Promise<void>;
}

const PrivateKnowledgeBaseDialogs: FC<PrivateKnowledgeBaseDialogsProps> = ({
  createFolderModalRef,
  onRefreshDirectory,
}) => (
  <>
    <CreateFolderModal ref={createFolderModalRef} refreshTable={onRefreshDirectory} />
  </>
);

export default PrivateKnowledgeBaseDialogs;
