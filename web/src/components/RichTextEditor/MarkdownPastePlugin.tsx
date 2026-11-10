import { useEffect } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { registerMarkdownPaste } from './registerMarkdownPaste';

/** Converts supported Markdown pasted into editor format nodes. */
export function MarkdownPastePlugin() {
  const [editor] = useLexicalComposerContext();

  useEffect(() => registerMarkdownPaste(editor), [editor]);

  return null;
}
