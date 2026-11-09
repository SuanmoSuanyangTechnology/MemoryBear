import { $createParagraphNode, $getRoot } from 'lexical';
import { parseMarkdownToNodes } from './markdownPaste';

// 在 editor.update 内用保存的 Markdown 替换内容，供初始值和外部赋值共用。
export const $setMarkdownValue = (value: string): void => {
  const root = $getRoot();
  const nodes = parseMarkdownToNodes(value);
  root.clear();
  root.append(...(nodes.length ? nodes : [$createParagraphNode()]));
};
