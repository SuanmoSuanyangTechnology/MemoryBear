import type { Model } from '@/views/ModelManagement/types';
import folderIcon from '@/assets/images/knowledgeBase/folder.svg';
import generalIcon from '@/assets/images/knowledgeBase/datasets.png';
import webIcon from '@/assets/images/knowledgeBase/general.png';
import tpIcon from '@/assets/images/application/text.svg';

export const baseModelFields = [
  { name: 'llm', type: 'llm', label: 'llmModel', required: true },
  { name: 'embedding', type: 'embedding', label: 'embeddingModel', required: true },
  { name: 'reranker', type: 'rerank', label: 'rerankModel', required: true },
] as const satisfies readonly {
  name: string;
  type: Model['type'];
  label: string;
  required: boolean;
}[];
export const multimodalModelFields = [
  { name: 'image2text', label: 'visionModel', type: 'vision', required: false },
  { name: 'audio2text', label: 'audioModel', type: 'audio', required: false },
  { name: 'video2text', label: 'videoModel', type: 'video', required: false },
] as const satisfies readonly {
  name: string;
  type: Model['type'];
  label: string;
  required: boolean;
}[];

// Get the icon by type
export const getTypeIcon = (type: string) => {
  const normalized = (type || '').toLowerCase();
  switch (normalized) {
    case 'general':
      return generalIcon;
    case 'folder':
      return folderIcon;
    case 'web':
      return webIcon;
    case 'third-party':
    case 'tp':
      return tpIcon;
    default:
      return generalIcon;
  }
};

// Dropdown menu items
export const menuItems = [
  {
    key: 'edit',
    icon: "rb:bg-[url('@/assets/images/common/edit_bold.svg')]",
  },
  {
    key: 'copy',
    icon: "rb:bg-[url('@/assets/images/common/copy_dark.svg')]",
  },
  {
    key: 'delete',
    icon: "rb:bg-[url('@/assets/images/common/delete_red_big.svg')]"
  }
]