import { Select } from 'antd';
import type { SelectProps } from 'antd';
import { useTranslation } from 'react-i18next';

import { DELIMITER_OPTIONS } from './constants';

/**
 * Reusable delimiter picker backed by {@link DELIMITER_OPTIONS}.
 *
 * Extracted to avoid repeating the i18n option mapping in the six chunking config spots
 * across the create-modal and upload-modal sections. Defaults to multiple selection to
 * match the `string[]` delimiters fields; pass `multiple={false}` for single select.
 */
interface DelimiterSelectProps extends Omit<SelectProps, 'mode' | 'options'> {
  multiple?: boolean;
}

const DelimiterSelect = ({ multiple = true, ...rest }: DelimiterSelectProps) => {
  const { t } = useTranslation();
  const options = DELIMITER_OPTIONS.map((o) => ({ ...o, label: t(o.label) }));

  return <Select maxCount={1} options={options} {...(multiple ? { mode: 'multiple' as const } : {})} {...rest} />;
};

export default DelimiterSelect;
