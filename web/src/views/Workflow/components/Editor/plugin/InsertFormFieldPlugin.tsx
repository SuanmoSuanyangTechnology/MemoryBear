import { useEffect, useCallback, useRef } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import {
  $getSelection,
  $isRangeSelection,
  $createTextNode,
  $createRangeSelection,
  $setSelection,
  $isTextNode,
  $createParagraphNode,
  $getRoot,
} from 'lexical';
import { $createFormFieldNode } from '../nodes/FormFieldNode';
import FormFieldEditModal, { type FormFieldEditModalRef } from '../nodes/FormFieldEditModal';
import { type FormFieldData } from '../nodes/FormFieldContext';
import type { Suggestion } from './AutocompletePlugin'

const InsertFormFieldPlugin = ({ formFields = [], updateFormFields, options = [] }: { formFields?: FormFieldData[]; updateFormFields: (formFields: FormFieldData[]) => void; options?: Suggestion[] }) => {
  const [editor] = useLexicalComposerContext();
  const modalRef = useRef<FormFieldEditModalRef>(null);
  const editorRef = useRef<HTMLDivElement | null>(null);

  const handleInsert = useCallback((id: string, defaultValue?: string, variableRef?: string) => {
    if (!id.trim()) return;

    const filteredField = formFields.find(field => field.id === id.trim());
    const fieldData: FormFieldData = filteredField || { id: id.trim(), default_value: defaultValue, variable_ref: variableRef };

    editor.update(() => {
      const selection = $getSelection();
      let anchorNode = null;
      let anchorOffset = 0;

      if (selection && $isRangeSelection(selection)) {
        anchorNode = selection.anchor.getNode();
        anchorOffset = selection.anchor.offset;
      }

      if (!anchorNode || !$isTextNode(anchorNode)) {
        const root = $getRoot();
        const children = root.getChildren();
        
        if (children.length > 0) {
          const lastChild = children[children.length - 1];
          const lastChildChildren = 'getChildren' in lastChild ? (lastChild as { getChildren(): any[] }).getChildren() : [];
          if (lastChildChildren.length > 0) {
            const lastTextNode = lastChildChildren[lastChildChildren.length - 1];
            if ($isTextNode(lastTextNode)) {
              anchorNode = lastTextNode;
              anchorOffset = lastTextNode.getTextContent().length;
            }
          }
        }
      }

      if ($isTextNode(anchorNode)) {
        const nodeText = anchorNode.getTextContent();
        const textBeforeCursor = nodeText.substring(0, anchorOffset);
        const textAfterCursor = nodeText.substring(anchorOffset);

        anchorNode.setTextContent(textBeforeCursor);

        const newParagraph = $createParagraphNode();
        const formFieldNode = $createFormFieldNode(fieldData.id, fieldData.default_value, fieldData.variable_ref);
        const trailingSpace = $createTextNode('\n');
        newParagraph.append(formFieldNode);
        newParagraph.append(trailingSpace);
        anchorNode.insertAfter(newParagraph);

        if (textAfterCursor) {
          const afterParagraph = $createParagraphNode();
          afterParagraph.append($createTextNode(textAfterCursor));
          trailingSpace.insertAfter(afterParagraph);
        }

        const newSelection = $createRangeSelection();
        newSelection.anchor.set(trailingSpace.getKey(), 1, 'text');
        newSelection.focus.set(trailingSpace.getKey(), 1, 'text');
        $setSelection(newSelection);
      } else {
        const root = $getRoot();
        const newParagraph = $createParagraphNode();
        const formFieldNode = $createFormFieldNode(fieldData.id, fieldData.default_value, fieldData.variable_ref);
        newParagraph.append(formFieldNode);
        root.append(newParagraph);
        root.append($createParagraphNode());
      }
    });

    if (!filteredField) {
      updateFormFields([...formFields, fieldData]);
    }
    modalRef.current?.close();
  }, [editor, formFields, updateFormFields]);

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === '/') {
      const target = e.target as HTMLElement;
      const isInEditor = target.closest('[data-lexical-editor]') || target.closest('.editor-with-line-numbers');
      if (isInEditor) {
        e.preventDefault();
        modalRef.current?.open();
      }
    }
  }, []);

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown);
    
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [handleKeyDown]);

  useEffect(() => {
    const interval = setInterval(() => {
      const editorElement = document.querySelector('[data-lexical-editor]') || document.querySelector('.editor-with-line-numbers');
      if (editorElement && editorElement !== editorRef.current) {
        editorRef.current = editorElement as HTMLDivElement;
      }
    }, 500);

    return () => clearInterval(interval);
  }, []);

  return (
    <FormFieldEditModal
      ref={modalRef}
      options={options}
      onSave={handleInsert}
    />
  );
};

export default InsertFormFieldPlugin;
