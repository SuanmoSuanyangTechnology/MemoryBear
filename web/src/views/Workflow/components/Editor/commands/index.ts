/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-23 12:29:46 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-03 10:12:48
 */
import { createCommand, type LexicalCommand } from 'lexical';
import type { Suggestion } from '../plugin/AutocompletePlugin';

// Payload interface for inserting variable command
export interface InsertVariableCommandPayload {
  data: Suggestion;
}

// Command to insert a variable into the editor
export const INSERT_VARIABLE_COMMAND: LexicalCommand<InsertVariableCommandPayload> = createCommand('INSERT_VARIABLE_COMMAND');

// Command to clear all editor content
export const CLEAR_EDITOR_COMMAND: LexicalCommand<void> = createCommand('CLEAR_EDITOR_COMMAND');

// Command to focus the editor
export const FOCUS_EDITOR_COMMAND: LexicalCommand<void> = createCommand('FOCUS_EDITOR_COMMAND');

// Command to close the autocomplete dropdown
export const CLOSE_AUTOCOMPLETE_COMMAND: LexicalCommand<void> = createCommand('CLOSE_AUTOCOMPLETE_COMMAND');