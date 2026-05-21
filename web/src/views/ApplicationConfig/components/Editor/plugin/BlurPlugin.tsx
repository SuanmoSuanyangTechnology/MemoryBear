/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-19 17:11:44 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-05-19 17:11:44 
 */
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { useEffect, useRef } from 'react';
import { $getRoot } from 'lexical';

interface BlurPluginProps {
  onBlur?: (value: string) => void;
}

export default function BlurPlugin({ onBlur }: BlurPluginProps) {
  const [editor] = useLexicalComposerContext();
  const onBlurRef = useRef(onBlur);
  const blurredRef = useRef(false);

  useEffect(() => {
    onBlurRef.current = onBlur;
  }, [onBlur]);

  useEffect(() => {
    return editor.registerRootListener((rootElement) => {
      if (rootElement) {
        const handleBlur = (event: FocusEvent) => {
          const relatedTarget = event.relatedTarget as HTMLElement | null;
          if (relatedTarget && rootElement.contains(relatedTarget)) {
            return;
          }
          
          if (blurredRef.current) return;
          blurredRef.current = true;
          
          const currentOnBlur = onBlurRef.current;
          if (currentOnBlur) {
            let text = '';
            editor.update(() => {
              const root = $getRoot();
              text = root.getTextContent();
            }, { discrete: true });
            currentOnBlur(text);
          }
          
          setTimeout(() => {
            blurredRef.current = false;
          }, 0);
        };
        rootElement.addEventListener('blur', handleBlur, true);
        return () => {
          rootElement.removeEventListener('blur', handleBlur, true);
        };
      }
      return () => {};
    });
  }, [editor]);

  return null;
}
