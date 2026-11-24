/**
 * FAQ 批量导入文案的统一取值入口，避免多处拼接同一个 i18n 命名空间。
 */
import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';

type FaqImportTextParams = Record<string, string | number>;

export default function useFaqImportI18n() {
  const { t } = useTranslation();
  return useCallback(
    (name: string, params?: FaqImportTextParams) =>
      t(`knowledgeBase.faq.importBatch.${name}`, params ?? {}),
    [t],
  );
}
