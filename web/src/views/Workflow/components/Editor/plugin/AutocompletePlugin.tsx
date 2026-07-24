/*
 * @Author: ZhaoYing
 * @Date: 2025-12-23 16:22:51
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-12 11:48:13
 */
import { type FC } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';

import { INSERT_VARIABLE_COMMAND } from '../commands';
import { useAutocompletePlugin } from './autocomplete/useAutocompletePlugin';
import AutocompletePopup from './autocomplete/AutocompletePopup';
import type { Suggestion } from './autocomplete/types';

// Re-export the shared Suggestion type so existing imports keep working
export type { Suggestion } from './autocomplete/types';

// Autocomplete plugin for variable suggestions triggered by '/' character
const AutocompletePlugin: FC<{ options: Suggestion[] }> = ({ options }) => {
  const [editor] = useLexicalComposerContext();

  const state = useAutocompletePlugin({
    editor,
    options,
    getShouldShow: (textBeforeCursor, anchorOffset) =>
      textBeforeCursor.endsWith('/') || (textBeforeCursor === '/' && anchorOffset === 1),
    doInsert: (suggestion) => {
      editor.dispatchCommand(INSERT_VARIABLE_COMMAND, { data: suggestion });
    },
  });

  return (
    <AutocompletePopup
      state={state}
      childPanelIdPrefix="autocomplete-child-panel-"
      listSizeClassName="rb:w-70 rb:h-57.5"
    />
  );
};

export default AutocompletePlugin;
