import type { ImportItem } from '@/api/faqImport';

interface RecordRow { cells: string[]; line: number }
function csvRecords(text: string): RecordRow[] {
  const records: RecordRow[] = [];
  let cells: string[] = [], field = '', quoted = false, closed = false;
  let line = 1, start = 1;
  const finish = () => {
    cells.push(field);
    if (cells.some(value => value.trim())) records.push({ cells, line: start });
    cells = []; field = ''; closed = false;
  };
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; } else { quoted = false; closed = true; }
      } else if (c === '\r' || c === '\n') {
        field += '\n'; line++;
        if (c === '\r' && text[i + 1] === '\n') i++;
      } else field += c;
    } else if (c === ',') {
      cells.push(field); field = ''; closed = false;
    } else if (c === '\r' || c === '\n') {
      finish(); line++; start = line;
      if (c === '\r' && text[i + 1] === '\n') i++;
    } else if (c === '"' && !field && !closed) quoted = true;
    else {
      if (closed || c === '"') throw new Error(`第 ${line} 行引号格式不正确`);
      field += c;
    }
  }
  if (quoted) throw new Error(`第 ${start} 行双引号未闭合`);
  finish();
  return records;
}
const asText = (value: unknown): string => value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
export async function parseFaqFile(file: File): Promise<ImportItem[]> {
  const ext = file.name.split('.').pop()?.toLowerCase();
  let records: RecordRow[];
  if (ext === 'json') {
    const data: unknown = JSON.parse((await file.text()).replace(/^\uFEFF/, ''));
    if (!Array.isArray(data)) throw new Error('JSON 顶层必须是数组');
    return data.map((row, index) => ({
      standard_question: asText(row?.question ?? row?.['问题'] ?? row?.q).trim(),
      standard_answer: asText(row?.answer ?? row?.['答案'] ?? row?.a).trim(),
      source_position: index + 1,
    }));
  }
  if (ext === 'xlsx') {
    const XLSX = await import('xlsx');
    const workbook = XLSX.read(await file.arrayBuffer(), { type: 'array' });
    const sheet = workbook.Sheets[workbook.SheetNames[0]];
    if (!sheet) throw new Error('文件没有可读取的工作表');
    const data = XLSX.utils.sheet_to_json<unknown[]>(sheet, { header: 1, defval: '', raw: false, blankrows: true, range: 0 });
    records = data.map((cells, i) => ({ cells: cells.map(asText), line: i + 1 })).filter(r => r.cells.some(v => v.trim()));
  } else if (ext === 'csv') {
    const text = (await file.text()).replace(/^\uFEFF/, '');
    if (text.includes('\uFFFD')) throw new Error('请将 CSV 保存为 UTF-8 后重新上传');
    records = csvRecords(text);
  } else throw new Error('请选择 CSV、XLSX 或 JSON 文件');
  const header = records[0]?.cells;
  if (header && /^(问题|question|q)$/i.test(header[0]?.trim())) {
    if (!/^(答案|answer|a)$/i.test(header[1]?.trim())) throw new Error('请使用问题、答案两列表头');
    records.shift();
  }
  return records.map(r => ({ standard_question: (r.cells[0] ?? '').trim(), standard_answer: (r.cells[1] ?? '').trim(), source_position: r.line }));
}

export async function downloadFaqTemplate(format: 'csv' | 'xlsx' | 'json') {
  const rows = [['问题', '答案'], ['如何创建知识库？', '在知识库列表点击新建。']];
  let content: BlobPart;
  let type: string;
  if (format === 'xlsx') {
    const XLSX = await import('xlsx');
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet(rows), 'FAQ');
    content = XLSX.write(workbook, { type: 'array', bookType: 'xlsx' });
    type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
  } else if (format === 'csv') {
    content = '\uFEFF' + rows.map(r => r.map(c => '"' + c.replaceAll('"', '""') + '"').join(',')).join('\r\n');
    type = 'text/csv;charset=utf-8';
  } else {
    content = JSON.stringify([{ question: rows[1][0], answer: rows[1][1] }], null, 2);
    type = 'application/json';
  }
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement('a'); link.href = url; link.download = `FAQ导入示例.${format}`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
