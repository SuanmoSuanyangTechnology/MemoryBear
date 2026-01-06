import { type FC, useEffect } from 'react';
import { $getRoot } from 'lexical';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';

// 处理换行的插件
const LineBreakPlugin: FC<{ onChange?: (value: string) => void }> = ({ onChange }) => {
  const [editor] = useLexicalComposerContext();
  
  useEffect(() => {
    return editor.registerUpdateListener(({ editorState }) => {
      editorState.read(() => {
        const root = $getRoot();
        const textContent = root.getTextContent();
        // 将\n转换为实际换行
        const processedContent = textContent.replace(/\\n/g, '\n');
        onChange?.(processedContent);
      });
    });
  }, [editor, onChange]);
  
  return null;
};

export default LineBreakPlugin;