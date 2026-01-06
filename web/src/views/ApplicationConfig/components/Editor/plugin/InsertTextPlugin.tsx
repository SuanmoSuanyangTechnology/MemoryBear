import { forwardRef, useImperativeHandle } from 'react';
import { $getSelection } from 'lexical';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import type { EditorRef } from '../index'

// 插入文本的插件
const InsertTextPlugin = forwardRef<EditorRef>((_, ref) => {
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