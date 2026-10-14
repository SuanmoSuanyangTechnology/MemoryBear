interface MarkdownNode {
  type: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
}

/** Transform text nodes only: code, existing links and raw HTML remain inert. */
export function remarkWikiLinks() {
  return (tree: MarkdownNode) => {
    const visit = (node: MarkdownNode) => {
      if (!node.children || ['link', 'image', 'code', 'inlineCode', 'html'].includes(node.type)) return;
      node.children = node.children.flatMap(child => {
        if (child.type !== 'text' || !child.value) {
          visit(child);
          return [child];
        }
        const pattern = /\[\[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\|([^\]\n]+)\]\]|\[\^(R\d+)\]/gi;
        const nodes: MarkdownNode[] = [];
        let offset = 0;
        for (const match of child.value.matchAll(pattern)) {
          const index = match.index!;
          if (index > offset) nodes.push({ type: 'text', value: child.value.slice(offset, index) });
          const ref = match[3]?.toUpperCase();
          nodes.push({
            type: 'link', url: ref ? `#wiki-source-${ref}` : `/wiki-page/${match[1]}`,
            children: [{ type: 'text', value: ref ? `[${ref}]` : match[2] }],
          });
          offset = index + match[0].length;
        }
        if (!nodes.length) return [child];
        if (offset < child.value.length) nodes.push({ type: 'text', value: child.value.slice(offset) });
        return nodes;
      });
    };
    visit(tree);
  };
}
