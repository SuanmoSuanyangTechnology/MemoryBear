/*
 * @Description: DocumentPreview state and per-file-type loading logic
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import mammoth from 'mammoth';
import * as XLSX from 'xlsx';
import * as pdfjsLib from 'pdfjs-dist';
import { useTranslation } from 'react-i18next';

import { cookieUtils } from '@/utils/request';
import {
  getFileExtension,
  isTextExt,
  isMarkdownExt,
  isImageExt,
  isPdfExt,
  isWordExt,
  isExcelExt,
  isCsvExt,
  isPptExt,
  getRequestUrl,
  fetchFileBuffer,
  fetchFileBufferWithLimit,
  IMAGE_MIME_MAP,
  CSV_PREVIEW_SIZE,
  MAX_PREVIEW_ROWS,
} from './utils';

// Configure the pdf.js worker via CDN to avoid Vite bundling dynamic import issues
pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.worker.min.mjs';

interface UseDocumentPreviewParams {
  fileUrl: string;
  fileName?: string;
  fileExt?: string;
}

export const useDocumentPreview = ({ fileUrl, fileName, fileExt }: UseDocumentPreviewParams) => {
  const { t } = useTranslation();
  const ext = getFileExtension(fileExt, fileName, fileUrl);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [textContent, setTextContent] = useState<string>('');
  const [htmlContent, setHtmlContent] = useState<string>('');
  const [excelData, setExcelData] = useState<{ sheetName: string; data: any[][] }[]>([]);
  const [csvTruncated, setCsvTruncated] = useState(false);

  // PDF state
  const [pdfDoc, setPdfDoc] = useState<pdfjsLib.PDFDocumentProxy | null>(null);
  const [pdfCurrentPage, setPdfCurrentPage] = useState(1);
  const [pdfTotalPages, setPdfTotalPages] = useState(0);
  const [pdfScale, setPdfScale] = useState(1.5);
  const pdfCanvasRef = useRef<HTMLCanvasElement>(null);
  const pdfRenderingRef = useRef(false);

  // PPT state
  const [pptSlides, setPptSlides] = useState<string[]>([]);
  const [pptCurrentPage, setPptCurrentPage] = useState(1);
  const [pptTotalPages, setPptTotalPages] = useState(0);

  // Image state
  const [imageBlobUrl, setImageBlobUrl] = useState<string>('');

  const handleError = (msg?: string) => {
    setLoading(false);
    setError(true);
    if (msg) setErrorMessage(msg);
  };

  // ========== PDF rendering ==========
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
      console.error('Failed to load PDF file:', err);
      handleError(err.message || t('knowledgeBase.pdfLoadFailed'));
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

  // ========== PPT/PPTX preview (convert to PDF, then render each page as an image with pdfjs) ==========
  const loadPptFile = useCallback(async () => {
    setLoading(true);
    setError(false);
    setErrorMessage('');
    try {
      const arrayBuffer = await fetchFileBuffer(fileUrl);
      // Try loading directly with pdfjs (some servers return a converted PDF)
      // If that fails, fall back to the Office Online Viewer
      try {
        const doc = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
        // Parsed as PDF successfully, render each page as an image
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
        // Not a PDF format, use the Office Online Viewer
        setPptSlides([]);
        setPptTotalPages(0);
        setLoading(false);
      }
    } catch (err: any) {
      console.error('Failed to load PPT file:', err);
      handleError(err.message || t('knowledgeBase.pptLoadFailedDesc'));
    }
  }, [fileUrl, t]);

  // ========== Image loading ==========
  const loadImageFile = async () => {
    setLoading(true);
    setError(false);
    setErrorMessage('');
    try {
      const arrayBuffer = await fetchFileBuffer(fileUrl);
      const mimeExt = ext.replace('.', '');
      const blob = new Blob([arrayBuffer], { type: IMAGE_MIME_MAP[mimeExt] || 'image/png' });
      const url = URL.createObjectURL(blob);
      setImageBlobUrl(url);
      setLoading(false);
    } catch (err: any) {
      console.error('Failed to load image file:', err);
      handleError(err.message || t('knowledgeBase.imageLoadFailed'));
    }
  };

  // ========== Text/Word/Excel loading ==========
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
        handleError(t('knowledgeBase.imagePreviewFailedDesc'));
        return;
      }
      const text = await response.text();
      if (text.startsWith('\x89PNG') || text.startsWith('�PNG')) {
        handleError(t('knowledgeBase.imagePreviewFailed'));
        return;
      }
      setTextContent(text);
      setLoading(false);
    } catch (err: any) {
      console.error('Failed to load text file:', err);
      handleError(err.message || t('knowledgeBase.textPreviewFailed'));
    }
  };

  const loadWordFile = async () => {
    setLoading(true);
    setError(false);
    setErrorMessage('');
    try {
      // mammoth does not support the legacy .doc format, use the Office Online Viewer
      if (ext === '.doc') {
        setHtmlContent('');
        setLoading(false);
        return;
      }
      const arrayBuffer = await fetchFileBuffer(fileUrl);
      // Validate that it is a valid docx (ZIP format, first two bytes are PK)
      const header = new Uint8Array(arrayBuffer.slice(0, 4));
      if (header[0] !== 0x50 || header[1] !== 0x4B) {
        // Not a ZIP/docx format, likely an HTML error page or JSON response
        const text = new TextDecoder().decode(arrayBuffer.slice(0, 200));
        throw new Error(`File content is not a valid docx format: ${text.substring(0, 100)}`);
      }
      const result = await mammoth.convertToHtml({ arrayBuffer });
      setHtmlContent(result.value);
      setLoading(false);
    } catch (err: any) {
      console.error('Failed to load Word file:', err);
      handleError(err.message || t('knowledgeBase.wordPreviewFailed'));
    }
  };

  const loadExcelFile = async () => {
    setLoading(true);
    setError(false);
    setErrorMessage('');
    setCsvTruncated(false);
    try {
      // CSV files may need encoding handling (possibly GBK/GB2312), and large files only take the first 1MB
      if (isCsvExt(ext)) {
        let arrayBuffer: ArrayBuffer;
        let truncated = false;
        try {
          // First try a Range request to fetch only the first 1MB
          arrayBuffer = await fetchFileBufferWithLimit(fileUrl, CSV_PREVIEW_SIZE);
          // If the returned data equals the size limit exactly, it may have been truncated
          if (arrayBuffer.byteLength >= CSV_PREVIEW_SIZE) {
            truncated = true;
          }
        } catch {
          // When Range requests are unsupported, fetch the whole file and truncate
          const fullBuffer = await fetchFileBuffer(fileUrl);
          if (fullBuffer.byteLength > CSV_PREVIEW_SIZE) {
            arrayBuffer = fullBuffer.slice(0, CSV_PREVIEW_SIZE);
            truncated = true;
          } else {
            arrayBuffer = fullBuffer;
          }
        }

        let csvText: string;
        const utf8Text = new TextDecoder('utf-8').decode(arrayBuffer);
        if (utf8Text.includes('\uFFFD') || /[\x80-\xff]/.test(utf8Text.slice(0, 200))) {
          try {
            csvText = new TextDecoder('gbk').decode(arrayBuffer);
          } catch {
            csvText = utf8Text;
          }
        } else {
          csvText = utf8Text;
        }

        // If truncated, drop the last incomplete row of data
        if (truncated) {
          const lastNewline = csvText.lastIndexOf('\n');
          if (lastNewline > 0) {
            csvText = csvText.substring(0, lastNewline);
          }
        }

        const workbook = XLSX.read(csvText, { type: 'string' });
        const sheets = workbook.SheetNames.map(sheetName => {
          const worksheet = workbook.Sheets[sheetName];
          let data = XLSX.utils.sheet_to_json(worksheet, { header: 1 }) as any[][];
          // Limit the maximum number of rows
          if (data.length > MAX_PREVIEW_ROWS + 1) {
            data = data.slice(0, MAX_PREVIEW_ROWS + 1); // +1 keeps the header row
            truncated = true;
          }
          return { sheetName, data };
        });
        setCsvTruncated(truncated);
        setExcelData(sheets);
        setLoading(false);
        return;
      }

      const arrayBuffer = await fetchFileBuffer(fileUrl);
      const workbook = XLSX.read(arrayBuffer, { type: 'array' });
      const sheets = workbook.SheetNames.map((sheetName: string) => {
        const worksheet = workbook.Sheets[sheetName];
        const data = XLSX.utils.sheet_to_json(worksheet, { header: 1 }) as any[][];
        return { sheetName, data };
      });
      setExcelData(sheets);
      setLoading(false);
    } catch (err: any) {
      console.error('Failed to load Excel file:', err);
      handleError(err.message || t('knowledgeBase.excelPreviewFailed'));
    }
  };

  const loadByType = () => {
    if (isTextExt(ext) || isMarkdownExt(ext)) loadTextFile();
    else if (isWordExt(ext)) loadWordFile();
    else if (isExcelExt(ext)) loadExcelFile();
    else if (isPdfExt(ext)) loadPdfFile();
    else if (isPptExt(ext)) loadPptFile();
    else if (isImageExt(ext)) loadImageFile();
  };

  const handleRetry = () => {
    setLoading(true);
    setError(false);
    setErrorMessage('');
    loadByType();
  };

  useEffect(() => {
    loadByType();
  }, [fileUrl]);

  // Re-render after PDF page change or zoom
  useEffect(() => {
    if (pdfDoc && isPdfExt(ext)) {
      renderPdfPage(pdfDoc, pdfCurrentPage, pdfScale);
    }
  }, [pdfCurrentPage, pdfScale, pdfDoc]);

  return {
    ext,
    loading,
    error,
    errorMessage,
    textContent,
    htmlContent,
    excelData,
    csvTruncated,
    // PDF
    pdfCanvasRef,
    pdfCurrentPage,
    pdfTotalPages,
    pdfScale,
    handlePdfPageChange,
    handlePdfZoom,
    // PPT
    pptSlides,
    pptCurrentPage,
    pptTotalPages,
    setPptCurrentPage,
    // Image
    imageBlobUrl,
    // Common
    handleRetry,
    handleError,
  };
};
