/*
 * @Author: ZhaoYing 
 * @Date: 2026-01-20 10:42:13 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-04-02 17:13:08 
 */
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { useEffect } from 'react';
import { CLOSE_AUTOCOMPLETE_COMMAND } from '../commands';

// Plugin to handle blur events and close autocomplete when clicking outside
export default function BlurPlugin() {
  const [editor] = useLexicalComposerContext();

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if ((e.target as HTMLElement)?.closest('[data-autocomplete-popup="true"]')) return;
      editor.dispatchCommand(CLOSE_AUTOCOMPLETE_COMMAND, undefined);
    };
    document.addEventListener('mousedown', handleClickOutside);

    return editor.registerRootListener((rootElement) => {
      if (rootElement) {
        return () => {
          document.removeEventListener('mousedown', handleClickOutside);
        };
      }
      return () => { document.removeEventListener('mousedown', handleClickOutside); };
    });
  }, [editor]);

  return null;
}
