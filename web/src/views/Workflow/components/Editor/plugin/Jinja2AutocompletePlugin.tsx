/*
 * @Author: ZhaoYing
 * @Date: 2026-04-02 17:10:59
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-07 14:50:14
 */
import { type FC } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $getSelection, $isRangeSelection, $isTextNode } from 'lexical';

import { useAutocompletePlugin } from './autocomplete/useAutocompletePlugin';
import AutocompletePopup from './autocomplete/AutocompletePopup';
import type { Suggestion } from './autocomplete/types';

const Jinja2AutocompletePlugin: FC<{ options: Suggestion[] }> = ({ options }) => {
  const [editor] = useLexicalComposerContext();

  const state = useAutocompletePlugin({
    editor,
    options,
    getShouldShow: (textBeforeCursor) => textBeforeCursor.endsWith('/'),
    doInsert: (suggestion) => {
      editor.update(() => {
        const selection = $getSelection();
        if (!$isRangeSelection(selection)) return;
        const anchorNode = selection.anchor.getNode();
        const anchorOffset = selection.anchor.offset;
        const nodeText = anchorNode.getTextContent();
        const textBefore = nodeText.substring(0, anchorOffset - 1);
        const textAfter = nodeText.substring(anchorOffset);
        const inserted = `{{${suggestion.value}}}`;
        if ($isTextNode(anchorNode)) {
          anchorNode.setTextContent(textBefore + inserted + textAfter);
          const newOffset = textBefore.length + inserted.length;
          selection.anchor.offset = newOffset;
          selection.focus.offset = newOffset;
        }
      });
      document.dispatchEvent(new CustomEvent('jinja2-variable-inserted', { detail: { value: suggestion.value } }));
    },
  });

  return (
    <AutocompletePopup
      state={state}
      childPanelIdPrefix="jinja2-autocomplete-child-panel-"
      listSizeClassName="rb:min-w-70 rb:max-h-57.5"
    />
  );
};

export default Jinja2AutocompletePlugin;
