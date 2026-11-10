import { $getRoot, $isElementNode, $isTextNode, type LexicalNode, type RangeSelection } from 'lexical';
import { $isListNode } from '@lexical/list';

/** Shared formatting rules for save and selection copy. Must be called within editor read context. */
export function generateMarkdown(selection?: RangeSelection): string {
  const selected = selection ? new Set(selection.getNodes().map(node => node.getKey())) : null;
  const start = selection?.isBackward() ? selection.focus : selection?.anchor;
  const end = selection?.isBackward() ? selection.anchor : selection?.focus;
  const processNode = (node: LexicalNode, depth = 0): string => {
    if ($isTextNode(node)) {
      if (selected && !selected.has(node.getKey())) return '';
      let text = node.getTextContent();
      const from = start?.key === node.getKey() ? start.offset : 0;
      const to = end?.key === node.getKey() ? end.offset : text.length;
      text = text.slice(from, to);
      if (!text) return '';
      // Output text content directly without adding format markers (text already contains Markdown markers)
      return text;
    }
    if ($isElementNode(node)) {
      if ($isListNode(node)) {
        return node.getChildren().map((item, index) => {
          const text = processNode(item, depth + 1);
          if (selection && !text) return '';
          const prefix = node.getTag() === 'ol' ? `${node.getStart() + index}. ` : '- ';
          return `${'  '.repeat(depth)}${prefix}${text}`;
        }).filter(Boolean).join('\n');
      }
      return node.getChildren().map(child => processNode(child, depth)).join('');
    }
    return selected && !selected.has(node.getKey()) ? '' : node.getTextContent();
  };
  return $getRoot().getChildren().map(node => processNode(node)).filter(text => text.trim()).join('\n\n');
}
