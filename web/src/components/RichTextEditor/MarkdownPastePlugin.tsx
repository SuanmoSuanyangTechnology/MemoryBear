import { useEffect } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { registerMarkdownPaste } from './registerMarkdownPaste';

/** 将支持的 Markdown 粘贴为编辑器格式节点。 */
export function MarkdownPastePlugin() {
  const [editor] = useLexicalComposerContext();

  useEffect(() => registerMarkdownPaste(editor), [editor]);

  return null;
}
