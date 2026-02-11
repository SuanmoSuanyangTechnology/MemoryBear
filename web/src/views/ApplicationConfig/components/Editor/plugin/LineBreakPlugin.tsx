/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:25:09 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:25:09 
 */
/**
 * Line Break Plugin
 * Handles line breaks and triggers onChange callback when editor content changes
 * Converts \n escape sequences to actual line breaks
 */

import { type FC, useEffect } from 'react';
import { $getRoot } from 'lexical';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';

/**
 * Plugin to handle line breaks and content changes
 */
const LineBreakPlugin: FC<{ onChange?: (value: string) => void }> = ({ onChange }) => {
  const [editor] = useLexicalComposerContext();
  
  useEffect(() => {
    return editor.registerUpdateListener(({ editorState }) => {
      editorState.read(() => {
        const root = $getRoot();
        const textContent = root.getTextContent();
        // Convert \n to actual line breaks
        const processedContent = textContent.replace(/\\n/g, '\n');
        onChange?.(processedContent);
      });
    });
  }, [editor, onChange]);
  
  return null;
};

export default LineBreakPlugin;