import React, { useState, useRef } from 'react';
import clsx from 'clsx';
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
  $getNodeByKey,
  $getRoot,
} from 'lexical';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { Flex, Space } from 'antd';

import { useFormFieldContext } from './FormFieldContext';
import FormFieldEditModal, { type FormFieldEditModalRef } from './FormFieldEditModal';

export type SerializedFormFieldNode = Spread<
  {
    id: string;
    default_value?: string;
    variable_ref?: string;
  },
  SerializedLexicalNode
>;

export function $isFormFieldNode(
  node: LexicalNode | null | undefined,
): node is FormFieldNode {
  return node instanceof FormFieldNode;
}

const FormFieldComponent: React.FC<{ nodeKey: NodeKey; id: string; default_value?: string; variable_ref?: string }> = ({
  nodeKey,
  id: initialId,
  default_value: initialDefaultValue,
  variable_ref: initialVariableRef,
}) => {
  const [editor] = useLexicalComposerContext();
  const { updateFormFields, formFields, options } = useFormFieldContext();
  const modalRef = useRef<FormFieldEditModalRef>(null);
  const [displayId, setDisplayId] = useState(initialId);
  const [displayDefaultValue, setDisplayDefaultValue] = useState(initialDefaultValue);
  // const [isDragging, setIsDragging] = useState(false);

  const handleDelete = () => {
    editor.update(() => {
      const root = $getRoot();
      const nodesToRemove: LexicalNode[] = [];
      
      const collectFormFieldNodes = (node: LexicalNode) => {
        if ($isFormFieldNode(node) && node.__id === displayId) {
          nodesToRemove.push(node);
        }
        const children = (node as any).getChildren?.();
        if (children) {
          children.forEach((child: LexicalNode) => collectFormFieldNodes(child));
        }
      };
      
      collectFormFieldNodes(root);
      
      nodesToRemove.forEach(node => node.remove());
    });
    
    const updatedFields = formFields.filter(f => f.id !== displayId);
    updateFormFields(updatedFields);
  };

  const handleEdit = () => {
    modalRef.current?.open(displayId, displayDefaultValue || '', initialVariableRef || '');
  };

  // const handleDragStart = (e: React.DragEvent) => {
  //   setIsDragging(true);
  //   e.dataTransfer.setData('nodeKey', nodeKey);
  //   e.dataTransfer.effectAllowed = 'move';
  // };

  // const handleDragEnd = () => {
  //   setIsDragging(false);
  // };
  const handleSave = (id: string, defaultValue?: string, variableRef?: string) => {
    if (!id.trim()) return;
    
    editor.update(() => {
      const node = $getNodeByKey(nodeKey) as FormFieldNode;
      if (node) {
        node.__id = id;
        node.__default_value = defaultValue;
        node.__variable_ref = variableRef;
      }
    });
    
    setDisplayId(id);
    setDisplayDefaultValue(defaultValue || '');
  }

  return (
    <>
      <Flex
        align="center"
        justify="space-between"
        gap={8}
        wrap={false}
        // draggable
        // onDragStart={handleDragStart}
        // onDragEnd={handleDragEnd}
        className={clsx(
          "rb:relative rb:mt-2! rb:max-w-full! rb:p-2! rb-border rb-border rb:rounded-lg rb:bg-white rb:text-[12px]",
          // { 'rb:opacity-50': isDragging }
        )}
        contentEditable={false}
      >
        <Space size={4} className="rb:bg-white rb:absolute rb:-top-2.5 rb:left-4">
          <span className="rb:text-blue-500">{`{x}`}</span>
          <span className="rb:text-gray-700 rb:font-medium">{displayId}</span>
        </Space>
        
        <div className="rb:flex-1 rb:min-w-0 rb:text-[12px] rb:overflow-hidden rb:text-ellipsis rb:whitespace-nowrap rb:text-gray-500">{displayDefaultValue}</div>
        <Flex align="center" gap={8} className="rb:flex rb:gap-0 rb:shrink-0">
          <div
            className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/edit.svg')]"
            
            onClick={(e) => {
              e.stopPropagation();
              handleEdit();
            }}
          ></div>
          <div
            className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/delete.svg')] rb:hover:bg-[url('@/assets/images/common/delete_hover.svg')]"
            onClick={(e) => {
              e.stopPropagation();
              handleDelete();
            }}
          ></div>
        </Flex>
      </Flex>

      <FormFieldEditModal
        ref={modalRef}
        initialId={displayId}
        options={options}
        onSave={handleSave}
      />
    </>
  );
};

export class FormFieldNode extends DecoratorNode<React.JSX.Element> {
  __id: string;
  __default_value?: string;
  __variable_ref?: string;

  static getType(): string {
    return 'form_field';
  }

  static clone(node: FormFieldNode): FormFieldNode {
    return new FormFieldNode(node.__id, node.__default_value, node.__variable_ref, node.__key);
  }

  constructor(id: string, default_value?: string, variable_ref?: string, key?: NodeKey) {
    super(key);
    this.__id = id;
    this.__default_value = default_value;
    this.__variable_ref = variable_ref;
  }

  createDOM(_config: EditorConfig): HTMLElement {
    const element = document.createElement('div');
    element.style.display = 'block';
    element.style.width = '100%';
    return element;
  }

  updateDOM(): false {
    return false;
  }

  decorate(): React.JSX.Element {
    return <FormFieldComponent nodeKey={this.__key} id={this.__id} default_value={this.__default_value} variable_ref={this.__variable_ref} />;
  }

  getTextContent(): string {
    return `{{form_field:${this.__id}}}`;
  }

  static importJSON(serializedNode: SerializedFormFieldNode): FormFieldNode {
    const { id, default_value, variable_ref } = serializedNode;
    return $createFormFieldNode(id, default_value, variable_ref);
  }

  exportJSON(): SerializedFormFieldNode {
    return {
      id: this.__id,
      default_value: this.__default_value,
      variable_ref: this.__variable_ref,
      type: 'form_field',
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

export function $createFormFieldNode(id: string, default_value?: string, variable_ref?: string): FormFieldNode {
  return $applyNodeReplacement(new FormFieldNode(id, default_value, variable_ref));
}
