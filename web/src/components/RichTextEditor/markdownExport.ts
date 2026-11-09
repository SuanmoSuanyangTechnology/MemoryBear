import { $getRoot, $isElementNode, $isTextNode, type LexicalNode, type RangeSelection } from 'lexical';
import { $isListNode } from '@lexical/list';

/** 保存与选区复制共用同一套格式规则。调用时须处于编辑器读取上下文。 */
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
      if (node.hasFormat('bold')) text = `**${text}**`;
      if (node.hasFormat('italic')) text = `*${text}*`;
      if (node.hasFormat('underline')) text = `<u>${text}</u>`;
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
