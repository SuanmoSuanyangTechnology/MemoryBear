import {
  $getSelection,
  $isRangeSelection,
  COMMAND_PRIORITY_HIGH,
  PASTE_COMMAND,
  type LexicalEditor,
} from 'lexical';
import { parseMarkdownToNodes } from './markdownPaste';

export function registerMarkdownPaste(editor: LexicalEditor) {
  return editor.registerCommand(PASTE_COMMAND, (event) => {
    if (!editor.isEditable() || !event || !('clipboardData' in event)) {
      return false;
    }
    const text = event.clipboardData?.getData('text/plain') || '';
    const selection = $getSelection();
    const hasFormatting = /\*\*.+?\*\*|\*[^*\n]+\*|<u>.+?<\/u>|^\s*(?:\d+\.|[-*])\s+\S/m.test(text);
    if (!hasFormatting || !$isRangeSelection(selection)) {
      return false;
    }

    const nodes = parseMarkdownToNodes(text);
    if (nodes.length === 0) {
      return false;
    }
    event.preventDefault();
    selection.insertNodes(nodes);
    return true;
  }, COMMAND_PRIORITY_HIGH);
}
