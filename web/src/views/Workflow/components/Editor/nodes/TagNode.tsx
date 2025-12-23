import {
  $applyNodeReplacement,
  DecoratorNode,
} from 'lexical';
import type { NodeKey, SerializedLexicalNode, Spread } from 'lexical';
import React from 'react';

export type SerializedTagNode = Spread<
  {
    label: string;
    tagType: string;
  },
  SerializedLexicalNode
>;

export class TagNode extends DecoratorNode<JSX.Element> {
  __label: string;
  __type: string;

  static getType(): string {
    return 'tagNode';
  }

  static clone(node: TagNode): TagNode {
    return new TagNode(node.__label, node.__type, node.__key);
  }

  constructor(label: string, type: string, key?: NodeKey) {
    super(key);
    this.__label = label;
    this.__type = type;
  }

  createDOM(): HTMLElement {
    return document.createElement('span');
  }

  updateDOM(): false {
    return false;
  }

  static importJSON(serializedNode: SerializedTagNode): TagNode {
    const { label, tagType } = serializedNode;
    return $createTagNode(label, tagType);
  }

  exportJSON(): SerializedTagNode {
    return {
      label: this.__label,
      tagType: this.__type,
      type: 'tagNode',
      version: 1,
    };
  }

  getTextContent(): string {
    return this.__label;
  }

  decorate(): JSX.Element {
    const getIconAndColor = (type: string) => {
      switch (type) {
        case 'context':
          return { icon: '📄', bgColor: '#722ed1' };
        case 'system':
          return { icon: 'x', bgColor: '#1890ff' };
        default:
          return { icon: 'x', bgColor: '#52c41a' };
      }
    };

    const { icon, bgColor } = getIconAndColor(this.__type);

    return (
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '4px',
          background: '#f0f8ff',
          border: '1px solid #d9d9d9',
          borderRadius: '4px',
          padding: '2px 6px',
          fontSize: '14px',
          margin: '0 2px',
        }}
      >
        <span
          style={{
            background: bgColor,
            color: 'white',
            padding: '1px 4px',
            borderRadius: '2px',
            fontSize: '10px',
            minWidth: '12px',
            textAlign: 'center',
          }}
        >
          {icon}
        </span>
        <span>{this.__label}</span>
      </span>
    );
  }
}

export function $createTagNode(label: string, type: string): TagNode {
  return new TagNode(label, type);
}

export function $isTagNode(node: any): node is TagNode {
  return node instanceof TagNode;
}