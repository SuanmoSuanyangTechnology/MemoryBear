/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2026-03-16 19:01:12
 * @LastEditors: yujiangping
 * @LastEditTime: 2026-03-17 16:19:45
 */
import { useState, useEffect, useRef, useCallback, type FC } from 'react';
import { Spin, Alert, Button, Table, InputNumber, Image } from 'antd';
import {
  ReloadOutlined,
  DownloadOutlined,
  LeftOutlined,
  RightOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
} from '@ant-design/icons';
import RbMarkdown from '../Markdown';
import { cookieUtils } from '@/utils/request';
import mammoth from 'mammoth';
import * as XLSX from 'xlsx';
import * as pdfjsLib from 'pdfjs-dist';
import pdfjsWorker from 'pdfjs-dist/build/pdf.worker.mjs?url';

// 设置 pdf.js worker
pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker;

interface DocumentPreviewProps {
  fileUrl: string;
  fileName?: string;
  fileExt?: string;
  width?: string | number;
  height?: string | number;
  className?: string;
}

const DocumentPreview: FC<DocumentPreviewProps> = ({
  fileUrl,
  fileName,
  fileExt,
  width = '100%',
  height = '600px',
  className = '',
}) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [textContent, setTextContent] = useState<string>('');
  const [htmlContent, setHtmlContent] = useState<string>('');
  const [excelData, setExcelData] = useState<{ sheetName: string; data: any[][] }[]>([]);

  // PDF 状态
  const [pdfDoc, setPdfDoc] = useState<pdfjsLib.PDFDocumentProxy | null>(null);
  const [pdfCurrentPage, setPdfCurrentPage] = useState(1);
  const [pdfTotalPages, setPdfTotalPages] = useState(0);
  const [pdfScale, setPdfScale] = useState(1.5);
  const pdfCanvasRef = useRef<HTMLCanvasElement>(null);
  const pdfRenderingRef = useRef(false);

  // PPT 状态
  const [pptSlides, setPptSlides] = useState<string[]>([]);
  const [pptCurrentPage, setPptCurrentPage] = useState(1);
  const [pptTotalPages, setPptTotalPages] = useState(0);

  // 图片状态
  const [imageBlobUrl, setImageBlobUrl] = useState<string>('');

  // 支持预览的文件类型
  const previewableTypes = [
    '.pdf', '.txt', '.md', '.csv',
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp',
    '.doc', '.docx', '.xls', '.xlsx',
    '.ppt', '.pptx',
  ];

  const getFileExtension = () => {
    if (fileExt) {
      return fileExt.toLowerCase().startsWith('.') ? fileExt.toLowerCase() : `.${fileExt.toLowerCase()}`;
    }
    const name = fileName || fileUrl;
    const match = name.match(/\.([^.]+)$/);
    return match ? `.${match[1].toLowerCase()}` : '';
  };

  const isTextFile = () => getFileExtension() === '.txt';
  const isMarkdownFile = () => getFileExtension() === '.md';
  const isImageFile = () => {
    const imageExts = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'];
    return imageExts.includes(getFileExtension());
  };
  const isPdfFile = () => getFileExtension() === '.pdf';
  const isWordFile = () => ['.doc', '.docx'].includes(getFileExtension());
  const isExcelFile = () => ['.xls', '.xlsx', '.csv'].includes(getFileExtension());
  const isPptFile = () => ['.ppt', '.pptx'].includes(getFileExtension());
  const isPreviewable = () => previewableTypes.includes(getFileExtension());

  const getRequestUrl = (url: string) => {
    if (url.includes('devapi.mem.redbearai.com')) {
      const parsed = new URL(url);
      return parsed.pathname;
    }
    return url;
  };

  const fetchFileBuffer = async (url: string): Promise<ArrayBuffer> => {
    const requestUrl = getRequestUrl(url);
    const response = await fetch(requestUrl, {
      credentials: 'include',
      headers: {
        'Authorization': `Bearer ${cookieUtils.get('authToken') || ''}`,
      },
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    return response.arrayBuffer();
  };

  const handleDownload = () => {
    const link = document.createElement('a');
    link.href = fileUrl;
    link.download = fileName || 'document';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const handleError = (msg?: string) => {
    setLoading(false);
    setError(true);
    if (msg) setErrorMessage(msg);
  };

  // ========== PDF 渲染逻辑 ==========
  const renderPdfPage = useCallback(async (doc: pdfjsLib.PDFDocumentProxy, pageNum: number, scale: number) => {
    if (pdfRenderingRef.current || !pdfCanvasRef.current) return;
    pdfRenderingRef.current = true;
    try {
      const page = await doc.getPage(pageNum);
      const viewport = page.getViewport({ scale });
      const canvas = pdfCanvasRef.current;
      const context = canvas.getContext('2d');
      if (!context) return;

      const dpr = window.devicePixelRatio || 1;
      canvas.width = viewport.width * dpr;
      canvas.height = viewport.height * dpr;
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);

      await page.render({ canvasContext: context, viewport }).promise;
    } finally {
      pdfRenderingRef.current = false;
    }
  }, []);

  const loadPdfFile = useCallback(async () => {
    setLoading(true);
    setError(false);
    setErrorMessage('');
    try {
      const arrayBuffer = await fetchFileBuffer(fileUrl);
      const doc = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
      setPdfDoc(doc);
      setPdfTotalPages(doc.numPages);
      setPdfCurrentPage(1);
      await renderPdfPage(doc, 1, pdfScale);
      setLoading(false);
    } catch (err: any) {
      console.error('加载 PDF 文件失败:', err);
      handleError(err.message || '加载 PDF 文件失败');
    }
  }, [fileUrl, pdfScale, renderPdfPage]);

  const handlePdfPageChange = async (page: number) => {
    if (!pdfDoc || page < 1 || page > pdfTotalPages) return;
    setPdfCurrentPage(page);
    await renderPdfPage(pdfDoc, page, pdfScale);
  };

  const handlePdfZoom = async (delta: number) => {
    const newScale = Math.max(0.5, Math.min(3, pdfScale + delta));
    setPdfScale(newScale);
    if (pdfDoc) {
      await renderPdfPage(pdfDoc, pdfCurrentPage, newScale);
    }
  };

  // ========== PPT/PPTX 预览逻辑（转 PDF 后用 pdfjs 渲染每页为图片） ==========
  const loadPptFile = useCallback(async () => {
    setLoading(true);
    setError(false);
    setErrorMessage('');
    try {
      const arrayBuffer = await fetchFileBuffer(fileUrl);
      // 尝试用 pdfjs 直接加载（某些服务端会返回转换后的 PDF）
      // 如果失败，则使用 Office Online Viewer 作为 fallback
      try {
        const doc = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
        // 成功解析为 PDF，逐页渲染为图片
        const slides: string[] = [];
        for (let i = 1; i <= doc.numPages; i++) {
          const page = await doc.getPage(i);
          const viewport = page.getViewport({ scale: 2 });
          const canvas = document.createElement('canvas');
          const context = canvas.getContext('2d');
          if (!context) continue;
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          await page.render({ canvasContext: context, viewport }).promise;
          slides.push(canvas.toDataURL('image/png'));
        }
        setPptSlides(slides);
        setPptTotalPages(slides.length);
        setPptCurrentPage(1);
        setLoading(false);
      } catch {
        // 不是 PDF 格式，使用 Office Online Viewer
        setPptSlides([]);
        setPptTotalPages(0);
        setLoading(false);
      }
    } catch (err: any) {
      console.error('加载 PPT 文件失败:', err);
      handleError(err.message || '加载 PPT 文件失败');
    }
  }, [fileUrl]);

  // ========== 图片加载逻辑 ==========
  const loadImageFile = async () => {
    setLoading(true);
    setError(false);
    setErrorMessage('');
    try {
      const arrayBuffer = await fetchFileBuffer(fileUrl);
      const ext = getFileExtension().replace('.', '');
      const mimeMap: Record<string, string> = {
        jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png',
        gif: 'image/gif', bmp: 'image/bmp', webp: 'image/webp', svg: 'image/svg+xml',
      };
      const blob = new Blob([arrayBuffer], { type: mimeMap[ext] || 'image/png' });
      const url = URL.createObjectURL(blob);
      setImageBlobUrl(url);
      setLoading(false);
    } catch (err: any) {
      console.error('加载图片文件失败:', err);
      handleError(err.message || '图片加载失败');
    }
  };

  // ========== 文本/Word/Excel 加载逻辑 ==========
  const loadTextFile = async () => {
    setLoading(true);
    setError(false);
    setErrorMessage('');
    try {
      const requestUrl = getRequestUrl(fileUrl);
      const response = await fetch(requestUrl, {
        credentials: 'include',
        headers: {
          'Authorization': `Bearer ${cookieUtils.get('authToken') || ''}`,
        },
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      const contentType = response.headers.get('Content-Type') || '';
      if (contentType.startsWith('image/')) {
        handleError('文件实际是图片类型，但被标记为文本文件');
        return;
      }
      const text = await response.text();
      if (text.startsWith('\x89PNG') || text.startsWith('�PNG')) {
        handleError('文件内容是图片，但扩展名是文本');
        return;
      }
      setTextContent(text);
      setLoading(false);
    } catch (err: any) {
      console.error('加载文本文件失败:', err);
      handleError(err.message || '加载文本文件失败');
    }
  };

  const loadWordFile = async () => {
    setLoading(true);
    setError(false);
    setErrorMessage('');
    try {
      const arrayBuffer = await fetchFileBuffer(fileUrl);
      const result = await mammoth.convertToHtml({ arrayBuffer });
      setHtmlContent(result.value);
      setLoading(false);
    } catch (err: any) {
      console.error('加载 Word 文件失败:', err);
      handleError(err.message || '加载 Word 文件失败，文件可能已损坏');
    }
  };

  const isCsvFile = () => getFileExtension() === '.csv';

  const loadExcelFile = async () => {
    setLoading(true);
    setError(false);
    setErrorMessage('');
    try {
      const arrayBuffer = await fetchFileBuffer(fileUrl);

      // CSV 文件需要处理编码问题（可能是 GBK/GB2312）
      if (isCsvFile()) {
        let csvText: string;
        // 先尝试 UTF-8 解码
        const utf8Text = new TextDecoder('utf-8').decode(arrayBuffer);
        // 检测是否有乱码特征（常见的 GBK 被错误解析为 UTF-8 的替换字符）
        if (utf8Text.includes('\uFFFD') || /[\x80-\xff]/.test(utf8Text.slice(0, 200))) {
          // 尝试 GBK 解码
          try {
            csvText = new TextDecoder('gbk').decode(arrayBuffer);
          } catch {
            csvText = utf8Text;
          }
        } else {
          csvText = utf8Text;
        }
        const workbook = XLSX.read(csvText, { type: 'string' });
        const sheets = workbook.SheetNames.map(sheetName => {
          const worksheet = workbook.Sheets[sheetName];
          const data = XLSX.utils.sheet_to_json(worksheet, { header: 1 }) as any[][];
          return { sheetName, data };
        });
        setExcelData(sheets);
        setLoading(false);
        return;
      }

      const workbook = XLSX.read(arrayBuffer, { type: 'array' });
      const sheets = workbook.SheetNames.map(sheetName => {
        const worksheet = workbook.Sheets[sheetName];
        const data = XLSX.utils.sheet_to_json(worksheet, { header: 1 }) as any[][];
        return { sheetName, data };
      });
      setExcelData(sheets);
      setLoading(false);
    } catch (err: any) {
      console.error('加载 Excel 文件失败:', err);
      handleError(err.message || '加载 Excel 文件失败，文件可能已损坏');
    }
  };

  const handleRetry = () => {
    setLoading(true);
    setError(false);
    setErrorMessage('');
    if (isTextFile() || isMarkdownFile()) loadTextFile();
    else if (isWordFile()) loadWordFile();
    else if (isExcelFile()) loadExcelFile();
    else if (isPdfFile()) loadPdfFile();
    else if (isPptFile()) loadPptFile();
  };

  useEffect(() => {
    if (isTextFile() || isMarkdownFile()) loadTextFile();
    else if (isWordFile()) loadWordFile();
    else if (isExcelFile()) loadExcelFile();
    else if (isPdfFile()) loadPdfFile();
    else if (isPptFile()) loadPptFile();
    else if (isImageFile()) loadImageFile();
  }, [fileUrl]);

  // PDF 翻页/缩放后重新渲染
  useEffect(() => {
    if (pdfDoc && isPdfFile()) {
      renderPdfPage(pdfDoc, pdfCurrentPage, pdfScale);
    }
  }, [pdfCurrentPage, pdfScale, pdfDoc]);

  // ========== 分页控制栏组件 ==========
  const PaginationBar = ({
    currentPage,
    totalPages,
    onPageChange,
    extraControls,
  }: {
    currentPage: number;
    totalPages: number;
    onPageChange: (page: number) => void;
    extraControls?: React.ReactNode;
  }) => (
    <div className="rb:flex rb:items-center rb:justify-center rb:gap-3 rb:py-2 rb:px-4 rb:bg-white rb:border-t rb:border-gray-200 rb:select-none">
      <Button
        size="small"
        icon={<LeftOutlined />}
        disabled={currentPage <= 1}
        onClick={() => onPageChange(currentPage - 1)}
      />
      <span className="rb:text-sm rb:text-gray-600 rb:flex rb:items-center rb:gap-1">
        <InputNumber
          size="small"
          min={1}
          max={totalPages}
          value={currentPage}
          onChange={(val) => val && onPageChange(val)}
          style={{ width: 56 }}
        />
        <span>/ {totalPages}</span>
      </span>
      <Button
        size="small"
        icon={<RightOutlined />}
        disabled={currentPage >= totalPages}
        onClick={() => onPageChange(currentPage + 1)}
      />
      {extraControls}
    </div>
  );

  if (!isPreviewable()) {
    return (
      <Alert
        message="不支持的文件类型"
        description={`仅支持预览：${previewableTypes.join(', ')}`}
        type="warning"
        showIcon
      />
    );
  }

  return (
    <div className={`rb:relative rb:flex rb:flex-col ${className}`} style={{ width, height }}>
      {loading && (
        <div className="rb:absolute rb:inset-0 rb:flex rb:items-center rb:justify-center rb:bg-gray-50 rb:z-10">
          <Spin size="large" tip="加载文档预览中..." />
        </div>
      )}

      {error && (
        <div className="rb:absolute rb:inset-0 rb:flex rb:items-center rb:justify-center rb:bg-gray-50 rb:z-10">
          <Alert
            message="预览失败"
            description={
              <div>
                <p className="rb:mb-2">无法加载文档预览</p>
                {errorMessage && (
                  <p className="rb:text-sm rb:text-red-600 rb:mb-3">错误详情：{errorMessage}</p>
                )}
                <p className="rb:text-sm rb:text-gray-600 rb:mb-3">可能的原因：</p>
                <ul className="rb:list-disc rb:pl-5 rb:text-sm rb:text-gray-600 rb:mb-3">
                  <li>文件 URL 无法访问（401/403/404）</li>
                  <li>认证 token 已过期</li>
                  <li>文件格式损坏或不匹配</li>
                  <li>网络连接问题</li>
                </ul>
                <div className="rb:mt-4 rb:flex rb:gap-2">
                  <Button icon={<ReloadOutlined />} onClick={handleRetry}>重试</Button>
                  <Button icon={<DownloadOutlined />} onClick={handleDownload}>下载文件</Button>
                </div>
              </div>
            }
            type="error"
            showIcon
          />
        </div>
      )}

      {/* 图片预览 */}
      {isImageFile() && !error && !loading && (
        <div className="rb:w-full rb:flex-1 rb:overflow-auto rb:bg-gray-50 rb:flex rb:items-center rb:justify-center">
          <Image
            src={imageBlobUrl}
            alt={fileName || '图片预览'}
            style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
            onError={() => handleError('图片渲染失败')}
          />
        </div>
      )}

      {/* Markdown 预览 */}
      {isMarkdownFile() && !error && !loading && (
        <div className="rb:w-full rb:flex-1 rb:overflow-auto rb:bg-white rb:p-6 rb:rounded rb:border rb:border-gray-200">
          <RbMarkdown content={textContent} />
        </div>
      )}

      {/* 文本预览 */}
      {isTextFile() && !error && !loading && (
        <div className="rb:w-full rb:flex-1 rb:overflow-auto rb:bg-white rb:p-4 rb:rounded rb:border rb:border-gray-200">
          <pre className="rb:whitespace-pre-wrap rb:text-sm rb:text-gray-800 rb:font-mono">
            {textContent}
          </pre>
        </div>
      )}

      {/* Word 预览 */}
      {isWordFile() && !error && !loading && (
        <div className="rb:w-full rb:flex-1 rb:overflow-auto rb:bg-white rb:p-6 rb:rounded rb:border rb:border-gray-200">
          <div
            className="rb:prose rb:max-w-none"
            dangerouslySetInnerHTML={{ __html: htmlContent }}
          />
        </div>
      )}

      {/* Excel 预览 */}
      {isExcelFile() && !error && !loading && (
        <div className="rb:w-full rb:flex-1 rb:overflow-auto rb:bg-white rb:p-4 rb:rounded rb:border rb:border-gray-200">
          {excelData.map((sheet, index) => (
            <div key={index} className="rb:mb-6">
              <h3 className="rb:text-lg rb:font-semibold rb:mb-3">{sheet.sheetName}</h3>
              {sheet.data.length > 0 && (
                <Table
                  dataSource={sheet.data.slice(1).map((row, idx) => ({ key: idx, ...row }))}
                  columns={sheet.data[0]?.map((header: any, colIdx: number) => ({
                    title: header || `列 ${colIdx + 1}`,
                    dataIndex: colIdx,
                    key: colIdx,
                    width: 150,
                  })) || []}
                  pagination={false}
                  scroll={{ x: 'max-content' }}
                  size="small"
                  bordered
                />
              )}
            </div>
          ))}
        </div>
      )}

      {/* PDF 预览 - 带分页和缩放 */}
      {isPdfFile() && !error && !loading && (
        <>
          <div className="rb:w-full rb:flex-1 rb:overflow-auto rb:bg-gray-100 rb:flex rb:justify-center rb:p-4">
            <canvas ref={pdfCanvasRef} className="rb:shadow-lg" />
          </div>
          {pdfTotalPages > 0 && (
            <PaginationBar
              currentPage={pdfCurrentPage}
              totalPages={pdfTotalPages}
              onPageChange={handlePdfPageChange}
              extraControls={
                <div className="rb:flex rb:items-center rb:gap-1 rb:ml-4">
                  <Button
                    size="small"
                    icon={<ZoomOutOutlined />}
                    disabled={pdfScale <= 0.5}
                    onClick={() => handlePdfZoom(-0.25)}
                  />
                  <span className="rb:text-sm rb:text-gray-600 rb:min-w-[48px] rb:text-center">
                    {Math.round(pdfScale * 100)}%
                  </span>
                  <Button
                    size="small"
                    icon={<ZoomInOutlined />}
                    disabled={pdfScale >= 3}
                    onClick={() => handlePdfZoom(0.25)}
                  />
                </div>
              }
            />
          )}
        </>
      )}

      {/* PPT/PPTX 预览 */}
      {isPptFile() && !error && !loading && (
        <>
          {pptSlides.length > 0 ? (
            /* 本地渲染模式（服务端返回了可解析的格式） */
            <>
              <div className="rb:w-full rb:flex-1 rb:overflow-auto rb:bg-gray-100 rb:flex rb:justify-center rb:items-center rb:p-4">
                <img
                  src={pptSlides[pptCurrentPage - 1]}
                  alt={`Slide ${pptCurrentPage}`}
                  className="rb:max-w-full rb:max-h-full rb:object-contain rb:shadow-lg"
                />
              </div>
              <PaginationBar
                currentPage={pptCurrentPage}
                totalPages={pptTotalPages}
                onPageChange={(page) => {
                  if (page >= 1 && page <= pptTotalPages) setPptCurrentPage(page);
                }}
              />
            </>
          ) : (
            /* Office Online Viewer fallback */
            <div className="rb:w-full rb:flex-1 rb:flex rb:flex-col">
              <iframe
                src={`https://view.officeapps.live.com/op/embed.aspx?src=${encodeURIComponent(fileUrl)}`}
                width="100%"
                height="100%"
                title={fileName || 'PPT 预览'}
                className="rb:border-0 rb:flex-1"
                style={{ border: 'none' }}
                onLoad={() => setLoading(false)}
                onError={() => handleError('PPT 在线预览加载失败')}
              />
              <div className="rb:flex rb:items-center rb:justify-center rb:gap-3 rb:py-2 rb:px-4 rb:bg-white rb:border-t rb:border-gray-200">
                <span className="rb:text-sm rb:text-gray-500">使用 Office Online 预览</span>
                <Button size="small" icon={<DownloadOutlined />} onClick={handleDownload}>
                  下载文件
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default DocumentPreview;
