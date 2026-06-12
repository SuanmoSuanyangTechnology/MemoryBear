/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-23 16:22:51 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-05 18:16:40
 */
import { useEffect, useRef } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $getRoot, $createParagraphNode, $createTextNode, $isParagraphNode } from 'lexical';

import { $createVariableNode } from '../nodes/VariableNode';
import { $createFormFieldNode, $isFormFieldNode } from '../nodes/FormFieldNode';
import { type Suggestion } from '../plugin/AutocompletePlugin'
import { type FormField } from '../index';

interface InitialValuePluginProps {
  value: string;
  options?: Suggestion[];
  onChange?: (value: string) => void;
  formFields?: FormField[]
}

const InitialValuePlugin: React.FC<InitialValuePluginProps> = ({ value, options = [], onChange, formFields = [] }) => {
  const [editor] = useLexicalComposerContext();
  const prevValueRef = useRef<string>('');
  const isUserInputRef = useRef(false);
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const formFieldsRef = useRef(formFields);
  formFieldsRef.current = formFields;
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    return editor.registerUpdateListener(({ editorState, tags }) => {
      if (tags.has('programmatic')) return;
      editorState.read(() => {
        const root = $getRoot();
        const paragraphs: string[] = [];
        root.getChildren().forEach(child => {
          if ($isParagraphNode(child)) {
            const paragraphText = child.getChildren().map(n => {
              if ($isFormFieldNode(n)) {
                return n.getTextContent();
              }
              return n.getTextContent();
            }).join('');
            paragraphs.push(paragraphText);
          }
        });
        const text = paragraphs.join('\n');
        if (text !== prevValueRef.current) {
          isUserInputRef.current = true;
          prevValueRef.current = text;
          onChangeRef.current?.(text);
        }
      });
    });
  }, [editor]);

  useEffect(() => {
    if (value !== prevValueRef.current) {
      if (isUserInputRef.current) {
        prevValueRef.current = value;
        isUserInputRef.current = false;
        return;
      }
      prevValueRef.current = value;
      isUserInputRef.current = false;

      queueMicrotask(() => {
        editor.update(() => {
          const root = $getRoot();
          root.clear();

          const parts = (value ?? '').split(/(\{\{[^}]+\}\}|\n)/);
          let paragraph = $createParagraphNode();

            parts.forEach(part => {
              if (part === '\n') {
                root.append(paragraph);
                paragraph = $createParagraphNode();
                return;
              }
              
              const formFieldMatch = part.match(/^\{\{form_field:([^}]+)\}\}$/);
              if (formFieldMatch) {
                const [_, fieldName] = formFieldMatch;
                const formField = formFieldsRef.current.find(f => f.id === fieldName);
                if (formField) {
                  paragraph.append($createFormFieldNode(fieldName, formField.default_value || formField.variable_ref));
                  return;
                }
                return;
              }

              const match = part.match(/^\{\{([^.]+)\.([^}]+)\}\}$/);
              const contextMatch = part.match(/^\{\{context\}\}$/);
              const conversationMatch = part.match(/^\{\{conv\.([^}]+)\}\}$/);
              const envMatch = part.match(/^\{\{env\.([^}]+)\}\}$/);
              const systemMatch = part.match(/^\{\{sys\.([^}]+)\}\}$/);

              if (contextMatch) {
                const contextSuggestion = optionsRef.current.find(s => s.isContext && s.label === 'context');
                if (contextSuggestion) {
                  paragraph.append($createVariableNode(contextSuggestion));
                } else {
                  paragraph.append($createTextNode(part));
                }
                return
              }

              if (conversationMatch) {
                const [_, variableName] = conversationMatch;
                const fullValue = `conv.${variableName}`;
                let conversationSuggestion = optionsRef.current.find(s =>
                  s.group === 'CONVERSATION' && s.label === variableName
                );
                if (!conversationSuggestion) {
                  for (const s of optionsRef.current) {
                    if (s.group === 'CONVERSATION' && s.children) {
                      const child = s.children.find(c => c.value === fullValue);
                      if (child) { conversationSuggestion = child; break; }
                    }
                  }
                }
                if (conversationSuggestion) {
                  paragraph.append($createVariableNode(conversationSuggestion));
                } else {
                  paragraph.append($createTextNode(part));
                }
                return
              }
              if (systemMatch) {
                const [_, variableName] = systemMatch;
                const fullValue = `sys.${variableName}`;
                // First try direct match on top-level label
                let systemSuggestion = optionsRef.current.find(s =>
                  s.group === 'SYSTEM' && s.label === fullValue
                );
                // Then search children by value (e.g. sys.api_key.url)
                if (!systemSuggestion) {
                  for (const s of optionsRef.current) {
                    if (s.group === 'SYSTEM' && s.children) {
                      const child = s.children.find(c => c.value === fullValue);
                      if (child) { systemSuggestion = child; break; }
                    }
                  }
                }
                if (systemSuggestion) {
                  paragraph.append($createVariableNode(systemSuggestion));
                } else {
                  paragraph.append($createTextNode(part));
                }
                return
              }
              if (envMatch) {
                const [_, variableName] = envMatch;
                const fullValue = `env.${variableName}`;
                // First try direct match on top-level label
                let envSuggestion = optionsRef.current.find(s =>
                  s.group === 'ENV' && s.label === fullValue
                );
                // Then search children by value (e.g. env.api_key.url)
                if (!envSuggestion) {
                  for (const s of optionsRef.current) {
                    if (s.group === 'ENV' && s.children) {
                      const child = s.children.find(c => c.value === fullValue);
                      if (child) { envSuggestion = child; break; }
                    }
                  }
                }
                if (envSuggestion) {
                  paragraph.append($createVariableNode(envSuggestion));
                } else {
                  paragraph.append($createTextNode(part));
                }
                return
              }

              if (match) {
                const [_, nodeId, rest] = match;
                const restParts = rest.split('.');
                const isThreeLevel = restParts.length >= 2;
                const parentLabel = isThreeLevel ? restParts.slice(0, -1).join('.') : undefined;
                const label = restParts[restParts.length - 1];

                let suggestion = optionsRef.current.find(s => {
                  return s.nodeData.id === nodeId && s.label === rest
                });

                if (!suggestion && isThreeLevel) {
                  for (const s of optionsRef.current) {
                    if (s.nodeData.id === nodeId && s.label === parentLabel && s.children) {
                      const child = s.children.find(c => c.label === label);
                      if (child) { suggestion = child; break; }
                    }
                  }
                }

                if (suggestion) {
                  paragraph.append($createVariableNode(suggestion));
                } else {
                  paragraph.append($createTextNode(part));
                }
              } else if (part) {
                paragraph.append($createTextNode(part));
              }
            });
            root.append(paragraph);
        }, { tag: 'programmatic' });
      });
    }
  }, [value, editor]);

  return null;
};

export default InitialValuePlugin;