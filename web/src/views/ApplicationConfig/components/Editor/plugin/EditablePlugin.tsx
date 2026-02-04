/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-04 11:20:49 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-04 11:20:49 
 */
import { useEffect } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';

/**
 * Props for the EditablePlugin component
 */
interface EditablePluginProps {
  /** Whether the editor should be disabled (read-only mode) */
  disabled?: boolean;
}

/**
 * EditablePlugin - A Lexical editor plugin that controls the editable state of the editor
 * 
 * This plugin allows you to dynamically toggle between editable and read-only modes.
 * When disabled is true, the editor becomes read-only and users cannot modify content.
 * When disabled is false or undefined, the editor is fully editable.
 * 
 * @param {EditablePluginProps} props - Component props
 * @param {boolean} [props.disabled] - Controls whether the editor is in read-only mode
 * @returns {null} This plugin doesn't render any UI elements
 * 
 * @example
 * ```tsx
 * <LexicalComposer>
 *   <EditablePlugin disabled={isReadOnly} />
 * </LexicalComposer>
 * ```
 */
export default function EditablePlugin({ disabled }: EditablePluginProps) {
  // Get the editor instance from Lexical composer context
  const [editor] = useLexicalComposerContext();

  // Update editor's editable state whenever the disabled prop changes
  useEffect(() => {
    // Set editor to editable when disabled is false, read-only when disabled is true
    editor.setEditable(!disabled);
  }, [editor, disabled]);

  // This plugin doesn't render any UI, it only manages editor state
  return null;
}
