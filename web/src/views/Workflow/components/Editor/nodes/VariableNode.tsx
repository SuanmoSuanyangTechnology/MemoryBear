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

  console.log('data', data)
  return (
    <span
      onClick={handleClick}
      className={clsx('rb:border rb:rounded-md rb:bg-white rb:text-[10px] rb:inline-flex rb:items-center rb:py-0 rb:px-1.5 rb:mx-0.5 rb:cursor-pointer', {
        'rb:border-[#171719]': isSelected,
        'rb:border-[#DFE4ED]': !isSelected
      })}
      contentEditable={false}
    >
      {data.isContext ? (
        <span style={{ fontSize: '12px', marginRight: '4px' }}>📄</span>
      ) : data.group !== 'CONVERSATION' && !data.value.includes('conv') ? (
        <span className={`rb:size-4 rb:mr-1 rb:bg-cover rb:inline-block rb:flex-shrink-0 ${data.nodeData?.icon}`} />
      ) : <span className="rb:inline-block rb:h-4"></span>}
      {!data.isContext && data.group !== 'CONVERSATION' && (
        <>
          {!data.value.includes('conv') && <>
            <span className="rb:wrap-break-word rb:line-clamp-1">{data.nodeData?.name}</span>
            <span style={{ color: '#DFE4ED', margin: '0 2px' }}>/</span>
          </>}
          {data.parentLabel && (
            <>
              <span className="rb:wrap-break-word rb:line-clamp-1">{data.parentLabel}</span>
              <span style={{ color: '#DFE4ED', margin: '0 2px' }}>/</span>
            </>
          )}
        </>
      )}
      <span className="rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap rb:flex-1 rb:text-[#171719]">{data.label}</span>
    </span>
  );
};

export class VariableNode extends DecoratorNode<React.JSX.Element> {
  __data: Suggestion;

  static getType(): string {
    return 'variable';
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
      type: 'variable',
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