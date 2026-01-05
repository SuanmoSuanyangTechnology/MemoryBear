import { createCommand, type LexicalCommand } from 'lexical';
import type { Suggestion } from '../plugin/AutocompletePlugin';


export interface InsertVariableCommandPayload {
  data: Suggestion;
}

export const INSERT_VARIABLE_COMMAND: LexicalCommand<InsertVariableCommandPayload> = createCommand('INSERT_VARIABLE_COMMAND');

export const CLEAR_EDITOR_COMMAND: LexicalCommand<void> = createCommand('CLEAR_EDITOR_COMMAND');

export const FOCUS_EDITOR_COMMAND: LexicalCommand<void> = createCommand('FOCUS_EDITOR_COMMAND');