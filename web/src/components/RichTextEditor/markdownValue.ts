import { $createParagraphNode, $getRoot } from 'lexical';
import { parseMarkdownToNodes } from './markdownPaste';

// Replace content within editor.update using saved Markdown, shared by initial value and external assignment.
export const $setMarkdownValue = (value: string): void => {
  const root = $getRoot();
  const nodes = parseMarkdownToNodes(value);
  root.clear();
  root.append(...(nodes.length ? nodes : [$createParagraphNode()]));
};
