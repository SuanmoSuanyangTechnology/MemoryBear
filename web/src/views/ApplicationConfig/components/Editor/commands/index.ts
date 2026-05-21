/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-19 17:11:13 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-05-19 17:11:13 
 */
import { createCommand, type LexicalCommand } from 'lexical';

export interface OptionItem {
  key?: string;
  label: string;
  value: string;
}

export interface InsertOptionCommandPayload {
  data: OptionItem;
}

export const INSERT_OPTION_COMMAND: LexicalCommand<InsertOptionCommandPayload> = createCommand('INSERT_OPTION_COMMAND');

export const CLOSE_AUTOCOMPLETE_COMMAND: LexicalCommand<void> = createCommand('CLOSE_AUTOCOMPLETE_COMMAND');
