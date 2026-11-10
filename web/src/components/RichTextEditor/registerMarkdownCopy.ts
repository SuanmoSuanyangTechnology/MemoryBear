import { $getSelection, $isRangeSelection, COMMAND_PRIORITY_HIGH, COPY_COMMAND, type LexicalEditor } from 'lexical';
import { generateMarkdown } from './markdownExport';

export function registerMarkdownCopy(editor: LexicalEditor) {
  return editor.registerCommand(COPY_COMMAND, event => {
    const selection = $getSelection();
    if (!$isRangeSelection(selection) || selection.isCollapsed() || !event || !('clipboardData' in event) || !event.clipboardData) return false;
    event.clipboardData.setData('text/plain', generateMarkdown(selection));
    event.preventDefault();
    return true;
  }, COMMAND_PRIORITY_HIGH);
}
