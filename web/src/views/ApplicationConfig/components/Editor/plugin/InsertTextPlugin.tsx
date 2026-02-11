/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:25:05 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:25:05 
 */
/**
 * Insert Text Plugin
 * Provides functionality to insert text at the current cursor position
 */

import { forwardRef, useImperativeHandle } from 'react';
import { $getSelection } from 'lexical';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';

/**
 * Plugin to insert text at cursor position
 */
const InsertTextPlugin = forwardRef<{ insertText: (text: string) => void; }>((_, ref) => {
  const [editor] = useLexicalComposerContext();
  
  useImperativeHandle(ref, () => ({
    insertText: (text: string) => {
      editor.update(() => {
        const selection = $getSelection();
        if (selection) {
          selection.insertText(text);
        }
      });
    }
  }), [editor]);
  
  return null;
});

export default InsertTextPlugin;