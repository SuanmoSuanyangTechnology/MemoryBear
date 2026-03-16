import { useState, useEffect, type FC } from 'react';
import { Spin, Alert, Button, Table } from 'antd';
import { ReloadOutlined, DownloadOutlined } from '@ant-design/icons';
import RbMarkdown from '../Markdown';
import { cookieUtils } from '@/utils/request';
import mammoth from 'mammoth';
import * as XLSX from 'xlsx';

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

  // 支持预览的文件类型
  const previewableTypes = ['.pdf', '.txt', '.md', '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.doc', '.docx', '.xls', '.xlsx'];
  // PPT 暂不支持
  const downloadOnlyTypes = ['.ppt', '.pptx'];
  
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
  const isExcelFile = () => ['.xls', '.xlsx'].includes(getFileExtension());
  const isPreviewable = () => previewableTypes.includes(getFileExtension());
  const isDownloadOnly = () => downloadOnlyTypes.includes(getFileExtension());

  const handleDownload = () => {
    const link = document.createElement('a');
    link.href = fileUrl;
    link.download = fileName || 'document';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const handleLoad = () => {
    setLoading(false);
    setError(false);
  };

  const handleError = (msg?: string) => {
    setLoading(false);
    setError(true);
    if (msg) setErrorMessage(msg);
  };

  const handleRetry = () => {
    setLoading(true);
    setError(false);
    setErrorMessage('');
    
    if (isTextFile() || isMarkdownFile()) {
      loadTextFile();
    } else if (isWordFile()) {
      loadWordFile();
    } else if (isExcelFile()) {
      loadExcelFile();
    } else {
      const iframe = document.querySelector(`iframe[title="${fileName || '文档预览'}"]`) as HTMLIFrameElement;
      if (iframe) {
        iframe.src = iframe.src;
      }
    }
  };

  const loadTextFile = async () => {
    setLoading(true);
    setError(false);
    setErrorMessage('');
    try {
      let requestUrl = fileUrl;
      
      if (fileUrl.includes('devapi.mem.redbearai.com')) {
        const url = new URL(fileUrl);
        requestUrl = url.pathname;
      }
      
      const response = await fetch(requestUrl, {
        credentials: 'include',
        headers: {
          'Authorization': `Bearer ${cookieUtils.get('authToken') || ''}`,
        },
      });
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      
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
      let requestUrl = fileUrl;
      
      if (fileUrl.includes('devapi.mem.redbearai.com')) {
        const url = new URL(fileUrl);
        requestUrl = url.pathname;
      }
      
      const response = await fetch(requestUrl, {
        credentials: 'include',
        headers: {
          'Authorization': `Bearer ${cookieUtils.get('authToken') || ''}`,
        },
      });
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      
      const arrayBuffer = await response.arrayBuffer();
      const result = await mammoth.convertToHtml({ arrayBuffer });
      setHtmlContent(result.value);
      setLoading(false);
    } catch (err: any) {
      console.error('加载 Word 文件失败:', err);
      handleError(err.message || '加载 Word 文件失败，文件可能已损坏');
    }
  };

  const loadExcelFile = async () => {
    setLoading(true);
    setError(false);
    setErrorMessage('');
    try {
      let requestUrl = fileUrl;
      
      if (fileUrl.includes('devapi.mem.redbearai.com')) {
        const url = new URL(fileUrl);
        requestUrl = url.pathname;
      }
      
      const response = await fetch(requestUrl, {
        credentials: 'include',
        headers: {
          'Authorization': `Bearer ${cookieUtils.get('authToken') || ''}`,
        },
      });
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      
      const arrayBuffer = await response.arrayBuffer();
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

  useEffect(() => {
    if (isTextFile() || isMarkdownFile()) {
      loadTextFile();
    } else if (isWordFile()) {
      loadWordFile();
    } else if (isExcelFile()) {
      loadExcelFile();
    }
  }, [fileUrl]);

  // PPT 文件只提供下载
  if (isDownloadOnly()) {
    return (
      <div className={`rb:relative rb:flex rb:items-center rb:justify-center rb:bg-gray-50 rb:rounded rb:border rb:border-gray-200 ${className}`} style={{ width, height }}>
        <Alert
          message="PowerPoint 文档预览"
          description={
            <div className="rb:text-center">
              <p className="rb:mb-4">PPT 文件暂不支持在线预览，请下载后查看</p>
              <Button 
                type="primary" 
                icon={<DownloadOutlined />} 
                onClick={handleDownload}
              >
                下载文件
              </Button>
            </div>
          }
          type="info"
          showIcon
        />
      </div>
    );
  }

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
    <div className={`rb:relative ${className}`} style={{ width, height }}>
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
                  <p className="rb:text-sm rb:text-red-600 rb:mb-3">
                    错误详情：{errorMessage}
                  </p>
                )}
                <p className="rb:text-sm rb:text-gray-600 rb:mb-3">可能的原因：</p>
                <ul className="rb:list-disc rb:pl-5 rb:text-sm rb:text-gray-600 rb:mb-3">
                  <li>文件 URL 无法访问（401/403/404）</li>
                  <li>认证 token 已过期</li>
                  <li>文件格式损坏或不匹配</li>
                  <li>网络连接问题</li>
                </ul>
                <div className="rb:mt-4 rb:flex rb:gap-2">
                  <Button icon={<ReloadOutlined />} onClick={handleRetry}>
                    重试
                  </Button>
                  <Button icon={<DownloadOutlined />} onClick={handleDownload}>
                    下载文件
                  </Button>
                </div>
              </div>
            }
            type="error"
            showIcon
          />
        </div>
      )}
      
      {isImageFile() && !error && !loading && (
        <div className="rb:w-full rb:h-full rb:overflow-auto rb:bg-gray-50 rb:flex rb:items-center rb:justify-center">
          <img 
            src={fileUrl} 
            alt={fileName || '图片预览'} 
            className="rb:max-w-full rb:max-h-full rb:object-contain"
            onError={() => handleError('图片加载失败')}
          />
        </div>
      )}

      {isMarkdownFile() && !error && !loading && (
        <div className="rb:w-full rb:h-full rb:overflow-auto rb:bg-white rb:p-6 rb:rounded rb:border rb:border-gray-200">
          <RbMarkdown content={textContent} />
        </div>
      )}

      {isTextFile() && !error && !loading && (
        <div className="rb:w-full rb:h-full rb:overflow-auto rb:bg-white rb:p-4 rb:rounded rb:border rb:border-gray-200">
          <pre className="rb:whitespace-pre-wrap rb:text-sm rb:text-gray-800 rb:font-mono">
            {textContent}
          </pre>
        </div>
      )}

      {isWordFile() && !error && !loading && (
        <div className="rb:w-full rb:h-full rb:overflow-auto rb:bg-white rb:p-6 rb:rounded rb:border rb:border-gray-200">
          <div 
            className="rb:prose rb:max-w-none"
            dangerouslySetInnerHTML={{ __html: htmlContent }}
          />
        </div>
      )}

      {isExcelFile() && !error && !loading && (
        <div className="rb:w-full rb:h-full rb:overflow-auto rb:bg-white rb:p-4 rb:rounded rb:border rb:border-gray-200">
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

      {isPdfFile() && !error && !loading && (
        <iframe
          src={fileUrl}
          width="100%"
          height="100%"
          title={fileName || 'PDF 预览'}
          className="rb:border-0"
          style={{ border: 'none' }}
          onLoad={handleLoad}
          onError={handleError}
        />
      )}
    </div>
  );
};

export default DocumentPreview;
