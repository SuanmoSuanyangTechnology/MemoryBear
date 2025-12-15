import { useState, useEffect, type FC } from 'react';
import { Spin, Alert, Button } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import RbMarkdown from '../Markdown';
import { cookieUtils } from '@/utils/request'

type PreviewMode = 'office' | 'google';

interface DocumentPreviewProps {
  fileUrl: string;
  fileName?: string;
  fileExt?: string; // 文件扩展名（优先使用）
  width?: string | number;
  height?: string | number;
  className?: string;
  mode?: PreviewMode; // 预览模式
  showModeSwitch?: boolean; // 是否显示模式切换按钮
}

const DocumentPreview: FC<DocumentPreviewProps> = ({
  fileUrl,
  fileName,
  fileExt,
  width = '100%',
  height = '600px',
  className = '',
  mode = 'office',
  showModeSwitch = true,
}) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [currentMode, setCurrentMode] = useState<PreviewMode>(mode);
  const [textContent, setTextContent] = useState<string>('');

  // 支持的文件类型
  const supportedTypes = ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.pdf', '.txt', '.md', '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'];
  
  // 获取文件扩展名（优先使用 fileExt prop）
  const getFileExtension = () => {
    if (fileExt) {
      return fileExt.toLowerCase().startsWith('.') ? fileExt.toLowerCase() : `.${fileExt.toLowerCase()}`;
    }
    const name = fileName || fileUrl;
    const match = name.match(/\.([^.]+)$/);
    return match ? `.${match[1].toLowerCase()}` : '';
  };
  
  // 检查是否为文本文件
  const isTextFile = () => {
    const ext = getFileExtension();
    return ext === '.txt';
  };
  
  // 检查是否为 Markdown 文件
  const isMarkdownFile = () => {
    const ext = getFileExtension();
    return ext === '.md';
  };
  
  // 检查是否为图片文件
  const isImageFile = () => {
    const ext = getFileExtension();
    const imageExts = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'];
    return imageExts.includes(ext);
  };
  
  // 检查文件类型是否支持
  const isSupportedFile = () => {
    const ext = getFileExtension();
    return ext && supportedTypes.includes(ext);
  };

  // 检查是否为 PDF 文件
  const isPdfFile = () => {
    const ext = getFileExtension();
    return ext === '.pdf';
  };

  // 构建预览 URL
  const getPreviewUrl = () => {
    // 处理文件 URL，如果是完整的 URL，转换为代理路径
    let requestUrl = fileUrl;
    
    // 如果是完整的 https://devapi.mem.redbearai.com 开头的 URL，提取路径部分
    // 这样可以通过代理访问，避免 CORS 问题
    if (fileUrl.includes('devapi.mem.redbearai.com')) {
      const url = new URL(fileUrl);
      requestUrl = url.pathname; // 只取路径部分，例如 /api/files/xxx
    }
    
    // 对于 PDF 文件，直接使用浏览器内置预览
    if (isPdfFile()) {
      return requestUrl;
    }
    
    // 确保 fileUrl 是完整的 URL（用于第三方预览服务）
    let fullUrl = fileUrl;
    if (!fileUrl.startsWith('http')) {
      fullUrl = `${window.location.origin}${fileUrl.startsWith('/') ? '' : '/'}${fileUrl}`;
    }
    console.log('预览 URL:', fullUrl);
    // 根据模式选择预览服务
    if (currentMode === 'google') {
      return `https://docs.google.com/viewer?url=${encodeURIComponent(fullUrl)}&embedded=true`;
    }
    
    // 默认使用 Microsoft Office Online Viewer
    return `https://view.officeapps.live.com/op/embed.aspx?src=${encodeURIComponent(fullUrl)}`;
  };

  const handleLoad = () => {
    setLoading(false);
    setError(false);
  };

  const handleError = () => {
    setLoading(false);
    setError(true);
  };

  const handleRetry = () => {
    setLoading(true);
    setError(false);
    
    if (isTextFile() || isMarkdownFile()) {
      // 重新加载文本文件
      loadTextFile();
    } else {
      // 强制重新加载 iframe
      const iframe = document.querySelector(`iframe[title="${fileName || '文档预览'}"]`) as HTMLIFrameElement;
      if (iframe) {
        iframe.src = iframe.src;
      }
    }
  };

  const handleSwitchMode = () => {
    setCurrentMode(prev => prev === 'office' ? 'google' : 'office');
    setLoading(true);
    setError(false);
  };

  // 加载文本文件内容
  const loadTextFile = async () => {
    setLoading(true);
    setError(false);
    try {
      // 处理文件 URL，如果是完整的 URL，转换为代理路径
      let requestUrl = fileUrl;
      
      // 如果是完整的 https://devapi.mem.redbearai.com 开头的 URL，提取路径部分
      if (fileUrl.includes('devapi.mem.redbearai.com')) {
        const url = new URL(fileUrl);
        requestUrl = url.pathname; // 只取路径部分，例如 /api/files/xxx
      }
      
      const response = await fetch(requestUrl, {
        credentials: 'include', // 包含认证信息
        headers: {
          'Authorization': `Bearer ${cookieUtils.get('authToken') || ''}`,
        },
      });
      
      if (!response.ok) {
        throw new Error('Failed to load file');
      }
      
      // 检查响应的 Content-Type
      const contentType = response.headers.get('Content-Type') || '';
      console.log('文件 Content-Type:', contentType);
      
      // 如果是图片类型，显示错误提示
      if (contentType.startsWith('image/')) {
        setError(true);
        setTextContent('');
        setLoading(false);
        console.error('文件实际是图片类型，但被标记为 txt');
        return;
      }
      
      const text = await response.text();
      
      // 检查是否是二进制数据（如 PNG 文件头）
      if (text.startsWith('\x89PNG') || text.startsWith('�PNG')) {
        setError(true);
        setTextContent('');
        setLoading(false);
        console.error('文件内容是 PNG 图片，但扩展名是 txt');
        return;
      }
      
      setTextContent(text);
      setLoading(false);
    } catch (err) {
      console.error('加载文本文件失败:', err);
      setError(true);
      setLoading(false);
    }
  };

  // 当文件是 txt 或 md 时，加载文本内容
  useEffect(() => {
    if (isTextFile() || isMarkdownFile()) {
      loadTextFile();
    }
  }, [fileUrl]);

  if (!isSupportedFile()) {
    return (
      <Alert
        message="不支持的文件类型"
        description={`仅支持以下文件类型：${supportedTypes.join(', ')}`}
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
                <p>无法加载文档预览，可能的原因：</p>
                <ul className="rb:list-disc rb:pl-5 rb:mt-2">
                  <li>文件需要认证访问，Office 预览服务无法访问</li>
                  <li>文件 URL 无法公开访问（需要配置公开访问或临时签名 URL）</li>
                  <li>文件大小超过限制（Office 预览通常限制 10MB）</li>
                  <li>预览服务暂时不可用</li>
                </ul>
                <p className="rb:mt-2 rb:text-gray-600">建议：请下载文件到本地查看</p>
                <div className="rb:mt-4 rb:flex rb:gap-2">
                  <Button icon={<ReloadOutlined />} onClick={handleRetry}>
                    重试
                  </Button>
                  {showModeSwitch && !isPdfFile() && (
                    <Button onClick={handleSwitchMode}>
                      切换到 {currentMode === 'office' ? 'Google' : 'Office'} 预览
                    </Button>
                  )}
                </div>
              </div>
            }
            type="error"
            showIcon
          />
        </div>
      )}
      
      {/* 图片文件预览 */}
      {isImageFile() && !error && !loading && (
        <div className="rb:w-full rb:h-full rb:overflow-auto rb:bg-gray-50 rb:flex rb:items-center rb:justify-center">
          <img 
            src={fileUrl} 
            alt={fileName || '图片预览'} 
            className="rb:max-w-full rb:max-h-full rb:object-contain"
            onError={() => setError(true)}
          />
        </div>
      )}

      {/* Markdown 文件预览 */}
      {isMarkdownFile() && !error && !loading && (
        <div className="rb:w-full rb:h-full rb:overflow-auto rb:bg-white rb:p-6 rb:rounded rb:border rb:border-gray-200">
          <RbMarkdown content={textContent} />
        </div>
      )}

      {/* 文本文件预览 */}
      {isTextFile() && !error && !loading && (
        <div className="rb:w-full rb:h-full rb:overflow-auto rb:bg-white rb:p-4 rb:rounded rb:border rb:border-gray-200">
          <pre className="rb:whitespace-pre-wrap rb:text-sm rb:text-gray-800 rb:font-mono">
            {textContent}
          </pre>
        </div>
      )}

      {/* PDF 文件预览（使用浏览器内置预览） */}
      {isPdfFile() && !error && !loading && (
        <iframe
          src={getPreviewUrl()}
          width="100%"
          height="100%"
          title={fileName || 'PDF 预览'}
          className="rb:border-0"
          style={{ border: 'none' }}
        />
      )}

      {/* Office 文件预览 */}
      {!isTextFile() && !isMarkdownFile() && !isImageFile() && !isPdfFile() && (
        <>
          {showModeSwitch && !loading && !error && (
            <div className="rb:absolute rb:top-2 rb:right-2 rb:z-20">
              <Button size="small" onClick={handleSwitchMode}>
                切换到 {currentMode === 'office' ? 'Google' : 'Office'} 预览
              </Button>
            </div>
          )}
          
          {!error && (
            <iframe
              src={getPreviewUrl()}
              width="100%"
              height="100%"
              onLoad={handleLoad}
              onError={handleError}
              title={fileName || '文档预览'}
              className="rb:border-0"
              style={{ display: loading ? 'none' : 'block', border: 'none' }}
              sandbox="allow-scripts allow-same-origin allow-popups"
            />
          )}
        </>
      )}
    </div>
  );
};

export default DocumentPreview;
