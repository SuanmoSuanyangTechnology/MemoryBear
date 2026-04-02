/*
 * @Author: ZhaoYing 
 * @Date: 2026-04-02 17:11:04 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-04-02 17:11:04 
 */
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { useEffect } from 'react';
import { $setSelection } from 'lexical';
import { CLOSE_AUTOCOMPLETE_COMMAND } from '../commands';

export default function Jinja2BlurPlugin() {
  const [editor] = useLexicalComposerContext();

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if ((e.target as HTMLElement)?.closest('[data-autocomplete-popup="true"]')) return;
      editor.dispatchCommand(CLOSE_AUTOCOMPLETE_COMMAND, undefined);
    };
    document.addEventListener('mousedown', handleClickOutside);

    return editor.registerRootListener((rootElement) => {
      if (rootElement) {
        const handleBlur = (e: FocusEvent) => {
          if ((e.target as HTMLElement)?.closest('[data-autocomplete-popup="true"]')) return;
          const relatedTarget = e.relatedTarget as HTMLElement;
          if (!relatedTarget || relatedTarget === document.body) return;
          editor.update(() => { $setSelection(null); });
        };
        rootElement.addEventListener('blur', handleBlur);
        return () => {
          document.removeEventListener('mousedown', handleClickOutside);
          rootElement.removeEventListener('blur', handleBlur);
        };
      }
      return () => { document.removeEventListener('mousedown', handleClickOutside); };
    });
  }, [editor]);

  return null;
}
