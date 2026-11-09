import { useEffect } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { registerMarkdownCopy } from './registerMarkdownCopy';

export function MarkdownCopyPlugin() {
  const [editor] = useLexicalComposerContext();
  useEffect(() => registerMarkdownCopy(editor), [editor]);
  return null;
}
