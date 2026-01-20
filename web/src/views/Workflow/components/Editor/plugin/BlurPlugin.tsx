import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { useEffect } from 'react';
import { $setSelection } from 'lexical';

export default function BlurPlugin() {
  const [editor] = useLexicalComposerContext();

  useEffect(() => {
    return editor.registerRootListener((rootElement) => {
      if (rootElement) {
        const handleBlur = (e: FocusEvent) => {
          // 检查是否点击了自动完成弹窗
          const target = e.target as HTMLElement;
          console.log('target', target)
          if (target?.closest('[data-autocomplete-popup="true"]')) {
            return;
          }
          
          editor.update(() => {
            $setSelection(null);
          });
        };

        rootElement.addEventListener('blur', handleBlur);
        return () => {
          rootElement.removeEventListener('blur', handleBlur);
        };
      }
    });
  }, [editor]);

  return null;
}
