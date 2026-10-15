import type { BreadcrumbItem } from '@/hooks/useBreadcrumbManager';

export const getFolderPathAfterEntering = (
  currentPath: BreadcrumbItem[],
  folder: Pick<BreadcrumbItem, 'id' | 'name'>,
): BreadcrumbItem[] => {
  const existingIndex = currentPath.findIndex((item) => item.id === folder.id);
  if (existingIndex < 0) {
    return [...currentPath, folder];
  }

  if (existingIndex === currentPath.length - 1) {
    return currentPath;
  }

  return currentPath.slice(0, existingIndex + 1);
};
