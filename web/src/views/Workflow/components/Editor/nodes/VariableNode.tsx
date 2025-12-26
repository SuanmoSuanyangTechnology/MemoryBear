import React from 'react';
import clsx from 'clsx'
import type {
  EditorConfig,
  LexicalNode,
  NodeKey,
  SerializedLexicalNode,
  Spread,
} from 'lexical';
import {
  $applyNodeReplacement,
  DecoratorNode,
} from 'lexical';
import { useLexicalNodeSelection } from '@lexical/react/useLexicalNodeSelection';
import type { Suggestion } from '../plugin/AutocompletePlugin';

export type SerializedVariableNode = Spread<
  {
    data: Suggestion;
  },
  SerializedLexicalNode
>;

const VariableComponent: React.FC<{ nodeKey: NodeKey; data: Suggestion }> = ({
  nodeKey,
  data,
}) => {
  const [isSelected, setSelected] = useLexicalNodeSelection(nodeKey);

  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setSelected(!isSelected);
  };

  return (
    <span
      onClick={handleClick}
      className={clsx('rb:border rb:rounded-md rb:bg-white rb:text-[12px] rb:inline-flex rb:items-center rb:py-0.5 rb:px-1.5 rb:mx-0.5 rb:cursor-pointer', {
        'rb:border-[#155EEF]': isSelected,
        'rb:border-[#DFE4ED]': !isSelected
      })}
      contentEditable={false}
    >
      <img 
        src={data.nodeData?.icon} 
        style={{ width: '12px', height: '12px', marginRight: '4px' }} 
        alt=""
      />
      {data.nodeData?.name}
      <span style={{ color: '#DFE4ED', margin: '0 2px' }}>/</span>
      <span style={{ color: '#155EEF' }}>{data.label}</span>
    </span>
  );
};

export class VariableNode extends DecoratorNode<React.JSX.Element> {
  __data: Suggestion;

  static getType(): string {
    return 'tag';
  }

  static clone(node: VariableNode): VariableNode {
    return new VariableNode(node.__data, node.__key);
  }

  constructor(data: Suggestion, key?: NodeKey) {
    super(key);
    this.__data = data;
  }

  createDOM(_config: EditorConfig): HTMLElement {
    const element = document.createElement('span');
    element.style.display = 'inline-block';
    return element;
  }

  updateDOM(): false {
    return false;
  }

  decorate(): React.JSX.Element {
    return <VariableComponent nodeKey={this.__key} data={this.__data} />;
  }

  getTextContent(): string {
    return `{{${this.__data?.value}}}`;
  }

  static importJSON(serializedNode: SerializedVariableNode): VariableNode {
    const { data } = serializedNode;
    return $createVariableNode(data);
  }

  exportJSON(): SerializedVariableNode {
    return {
      data: this.__data,
      type: 'tag',
      version: 1,
    };
  }

  canInsertTextBefore(): boolean {
    return false;
  }

  canInsertTextAfter(): boolean {
    return false;
  }

  canBeEmpty(): boolean {
    return false;
  }

  isInline(): true {
    return true;
  }

  isKeyboardSelectable(): boolean {
    return true;
  }
}

export function $createVariableNode(data: Suggestion): VariableNode {
  return $applyNodeReplacement(new VariableNode(data));
}

export function $isVariableNode(
  node: LexicalNode | null | undefined,
): node is VariableNode {
  return node instanceof VariableNode;
}