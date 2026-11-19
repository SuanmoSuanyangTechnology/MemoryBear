/**
 * Local parsing and sample-file generation for FAQ batch import.
 * Front-end validation only checks required fields; all business validation
 * (length, types, etc.) is left to the backend.
 */
import * as XLSX from 'xlsx';

import i18n from '@/i18n';
import type { FaqImportItem } from './types';

export type FaqImportIssueCode =
  | 'unsupportedFormat'
  | 'noSheet'
  | 'emptySheet'
  | 'encodingError'
  | 'readFailed'
  | 'emptyBatch'
  | 'badHeader'
  | 'invalidJson'
  | 'jsonNotArray'
  | 'questionRequired'
  | 'answerRequired';

export interface FaqImportIssue {
  code: FaqImportIssueCode;
  params?: Record<string, string | number>;
}

export interface FaqImportRow {
  source_position: number;
  standard_question: string;
  standard_answer: string;
  errors: FaqImportIssue[];
}

export interface FaqImportParseResult {
  rows: FaqImportRow[];
  error: FaqImportIssue | null;
  isJson: boolean;
}

export type FaqImportTemplateFormat = 'csv' | 'xlsx' | 'json';

interface FaqImportRawRecord {
  line: number;
  cells: unknown[];
}

const SUPPORTED_FILE_NAME = /\.(csv|xlsx|json)$/i;
const QUESTION_HEADERS = ['问题', 'question', 'q'];
const ANSWER_HEADERS = ['答案', 'answer', 'a'];

const issue = (code: FaqImportIssueCode, params?: Record<string, string | number>): FaqImportIssue => ({ code, params });

const toText = (value: unknown): string => (value == null ? '' : String(value).trim());

export const fileImportError = (code: FaqImportIssueCode, params?: Record<string, string | number>): FaqImportParseResult => ({
  rows: [],
  error: issue(code, params),
  isJson: false,
});

/** Only the question and answer are required; other rows pass through. */
function validate(records: FaqImportRawRecord[], isJson: boolean): FaqImportParseResult {
  const rows = records.map<FaqImportRow>((record) => {
    const standard_question = toText(record.cells[0]);
    const standard_answer = toText(record.cells[1]);
    const errors: FaqImportIssue[] = [];
    if (!standard_question) errors.push(issue('questionRequired'));
    if (!standard_answer) errors.push(issue('answerRequired'));
    return { source_position: record.line, standard_question, standard_answer, errors };
  });
  return {
    rows,
    error: rows.length ? null : issue('emptyBatch'),
    isJson,
  };
}

/** Recognize and skip the "question,answer" header; treat the rest as data. */
function fromRecords(records: FaqImportRawRecord[]): FaqImportParseResult {
  const first = records[0];
  const firstCell = toText(first?.cells[0]).toLowerCase();
  if (first && QUESTION_HEADERS.includes(firstCell)) {
    if (first.cells.length !== 2 || !ANSWER_HEADERS.includes(toText(first.cells[1]).toLowerCase())) {
      return fileImportError('badHeader', { line: first.line });
    }
    return validate(records.slice(1), false);
  }
  return validate(records, false);
}

/** Parse CSV with comma/tab delimiters, escaped quotes and original line numbers. */
function parseCsvText(input: string): FaqImportParseResult {
  const text = input.replace(/^\uFEFF/, '');
  let delimiter = ',';
  let inQuote = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (char === '"') {
      if (inQuote && text[i + 1] === '"') i++;
      else inQuote = !inQuote;
    }
    if (!inQuote && (char === ',' || char === '\t')) {
      delimiter = char;
      break;
    }
    if (!inQuote && (char === '\n' || char === '\r') && text.slice(0, i).trim()) break;
  }

  const records: FaqImportRawRecord[] = [];
  let cells: unknown[] = [];
  let field = '';
  let mode: 'start' | 'plain' | 'quoted' | 'closed' = 'start';
  let line = 1;
  let startLine = 1;
  let startOffset = 0;

  const finish = (end: number) => {
    cells.push(field);
    if (text.slice(startOffset, end).trim()) records.push({ line: startLine, cells });
    cells = [];
    field = '';
    mode = 'start';
  };

  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    const newline = char === '\n' || char === '\r';
    if (mode === 'quoted') {
      if (char === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else mode = 'closed';
      } else if (newline) {
        field += '\n';
        if (char === '\r' && text[i + 1] === '\n') i++;
        line++;
      } else field += char;
      continue;
    }
    if (newline) {
      finish(i);
      if (char === '\r' && text[i + 1] === '\n') i++;
      line++;
      startLine = line;
      startOffset = i + 1;
    } else if (char === delimiter) {
      cells.push(field);
      field = '';
      mode = 'start';
    } else if (char === '"' && mode === 'start') {
      mode = 'quoted';
    } else {
      field += char;
      mode = 'plain';
    }
  }
  finish(text.length);
  return fromRecords(records);
}

/** Parse a top-level JSON array; non-object items fail the required-field check. */
function parseJsonText(input: string): FaqImportParseResult {
  let items: unknown;
  try {
    items = JSON.parse(input.replace(/^\uFEFF/, ''));
  } catch {
    return fileImportError('invalidJson');
  }
  if (!Array.isArray(items)) return fileImportError('jsonNotArray');

  const records = items.map<FaqImportRawRecord>((item, index) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      return { line: index + 1, cells: [] };
    }
    const data = item as Record<string, unknown>;
    return {
      line: index + 1,
      cells: [data.question ?? data['问题'] ?? data.q, data.answer ?? data['答案'] ?? data.a],
    };
  });
  return validate(records, true);
}

/** Read only the first XLSX worksheet; blank rows keep their original line number. */
function parseWorkbook(buffer: ArrayBuffer): FaqImportParseResult {
  const workbook = XLSX.read(buffer, { type: 'array' });
  const sheet = workbook.Sheets[workbook.SheetNames[0]];
  if (!sheet) return fileImportError('noSheet');
  if (!sheet['!ref']) return fileImportError('emptySheet');
  const data = XLSX.utils.sheet_to_json<unknown[]>(sheet, {
    header: 1,
    defval: '',
    raw: false,
    blankrows: true,
    range: 0,
  });
  const records = data
    .map<FaqImportRawRecord>((cells, index) => ({ line: index + 1, cells }))
    .filter((record) => record.cells.some((value) => toText(value)));
  return fromRecords(records);
}

/** Parse a single file in the browser and map read failures to whole-file errors. */
export async function parseFaqImportFile(file: File): Promise<FaqImportParseResult> {
  try {
    const name = file.name;
    if (!SUPPORTED_FILE_NAME.test(name)) return fileImportError('unsupportedFormat');
    if (/\.xlsx$/i.test(name)) {
      const buffer = await file.arrayBuffer();
      return parseWorkbook(buffer);
    }
    const text = await file.text();
    if (text.includes('\uFFFD') || text.includes('\0')) return fileImportError('encodingError');
    return /\.json$/i.test(name) ? parseJsonText(text) : parseCsvText(text);
  } catch {
    return fileImportError('readFailed');
  }
}

export const getFaqImportFailures = (result: FaqImportParseResult): FaqImportRow[] =>
  result.rows.filter((row) => row.errors.length > 0);

export const isFaqImportReady = (result: FaqImportParseResult | null): boolean =>
  Boolean(result && result.rows.length > 0 && !result.error && getFaqImportFailures(result).length === 0);

export const toFaqImportItems = (result: FaqImportParseResult): FaqImportItem[] =>
  result.rows.map(({ standard_question, standard_answer, source_position }) => ({
    standard_question,
    standard_answer,
    source_position,
  }));

interface FaqImportTemplateFile {
  name: string;
  mime: string;
  content: BlobPart;
}

/** Build sample files in all three supported formats. */
export function buildFaqImportTemplate(format: FaqImportTemplateFormat): FaqImportTemplateFile {
  const key = (name: string) => i18n.t(`knowledgeBase.faq.importBatch.${name}`);
  const headers = [key('templateHeaderQuestion'), key('templateHeaderAnswer')];
  const examples = [
    [key('templateQuestion1'), key('templateAnswer1')],
    [key('templateQuestion2'), key('templateAnswer2')],
  ];
  const rows = [headers, ...examples];
  const name = `${key('templateFileName')}.${format}`;
  if (format === 'csv') {
    const content = '\uFEFF' + rows.map((row) => row.map((value) => `"${value.replace(/"/g, '""')}"`).join(',')).join('\r\n');
    return { name, mime: 'text/csv;charset=utf-8', content };
  }
  if (format === 'json') {
    const content = JSON.stringify(examples.map(([question, answer]) => ({ question, answer })), null, 2);
    return { name, mime: 'application/json;charset=utf-8', content };
  }
  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet(rows), 'FAQ');
  return {
    name,
    mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    content: XLSX.write(workbook, { bookType: 'xlsx', type: 'array' }),
  };
}

/** Trigger the sample download and release the Blob URL promptly. */
export function downloadFaqImportTemplate(format: FaqImportTemplateFormat): void {
  const file = buildFaqImportTemplate(format);
  const url = URL.createObjectURL(new Blob([file.content], { type: file.mime }));
  const link = document.createElement('a');
  link.href = url;
  link.download = file.name;
  link.style.display = 'none';
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
