/*
 * @Author: ZhaoYing 
 * @Date: 2026-01-20 10:42:13 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-03 10:12:10
 */
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { useEffect } from 'react';
import { $setSelection } from 'lexical';
import { CLOSE_AUTOCOMPLETE_COMMAND } from '../commands';

// Plugin to handle blur events and close autocomplete when clicking outside
export default function BlurPlugin({ enableJinja2 }: { enableJinja2: boolean }) {
  const [editor] = useLexicalComposerContext();

  useEffect(() => {
    // Close autocomplete when clicking outside the popup
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (target?.closest('[data-autocomplete-popup="true"]')) {
        return;
      }
      editor.dispatchCommand(CLOSE_AUTOCOMPLETE_COMMAND, undefined);
    };

    document.addEventListener('mousedown', handleClickOutside);

    return editor.registerRootListener((rootElement) => {
      if (rootElement) {
        const handleBlur = (e: FocusEvent) => {
          if (enableJinja2) {
            // Check if autocomplete popup was clicked
            const target = e.target as HTMLElement;
            if (target?.closest('[data-autocomplete-popup="true"]')) {
              return;
            }

            // Check if blur was caused by paste operation
            const relatedTarget = e.relatedTarget as HTMLElement;
            if (!relatedTarget || relatedTarget === document.body) {
              return;
            }
            
            // Clear selection on blur
            editor.update(() => {
              $setSelection(null);
            });
          }
        };

        rootElement.addEventListener('blur', handleBlur);
        return () => {
          document.removeEventListener('mousedown', handleClickOutside);
          rootElement.removeEventListener('blur', handleBlur);
        };
      }
      return () => {
        document.removeEventListener('mousedown', handleClickOutside);
      };
    });
  }, [editor, enableJinja2]);

  return null;
}
