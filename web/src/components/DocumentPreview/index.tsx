/*
 * @Description:
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2026-03-16 19:01:12
 * @LastEditors: yujiangping
 * @LastEditTime: 2026-03-20 12:12:20
 */
import { type FC } from 'react';
import { Spin, Alert, Button, Table, Image, Flex } from 'antd';
import {
  ReloadOutlined,
  DownloadOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
} from '@ant-design/icons';
import { useTranslation } from 'react-i18next';

import RbMarkdown from '../Markdown';
import TextVirtualList from './TextVirtualList';
import PaginationBar from './PaginationBar';
import { useDocumentPreview } from './useDocumentPreview';
import {
  previewableTypes,
  isTextExt,
  isMarkdownExt,
  isImageExt,
  isPdfExt,
  isWordExt,
  isExcelExt,
  isPptExt,
  isPreviewableExt,
  MAX_PREVIEW_ROWS,
} from './utils';

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
  const { t } = useTranslation();
  const {
    ext,
    loading,
    error,
    errorMessage,
    textContent,
    htmlContent,
    excelData,
    csvTruncated,
    pdfCanvasRef,
    pdfCurrentPage,
    pdfTotalPages,
    pdfScale,
    handlePdfPageChange,
    handlePdfZoom,
    pptSlides,
    pptCurrentPage,
    pptTotalPages,
    setPptCurrentPage,
    imageBlobUrl,
    handleRetry,
    handleError,
  } = useDocumentPreview({ fileUrl, fileName, fileExt });

  const handleDownload = () => {
    const link = document.createElement('a');
    link.href = fileUrl;
    link.download = fileName || 'document';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  if (!isPreviewableExt(ext)) {
    return (
      <Alert
        message={t('knowledgeBase.fileAcceptTip')}
        description={`${t('knowledgeBase.previewableTypes')}${previewableTypes.join(', ')}`}
        type="warning"
        showIcon
      />
    );
  }

  return (
    <Flex vertical className={`rb:relative ${className}`} style={{ width, height }}>
      {loading && (
        <Flex align="center" justify="center" className="rb:absolute rb:inset-0 rb:bg-gray-50 rb:z-10">
          <Spin size="large" tip={t('knowledgeBase.loadingPreview')}>
            <div className="rb:w-50 rb:h-16" />
          </Spin>
        </Flex>
      )}

      {error && (
        <Flex align="center" justify="center" className="rb:absolute rb:inset-0 rb:bg-gray-50 rb:z-10">
          <Alert
            message={t('knowledgeBase.previewFailed')}
            description={
              <div>
                <p className="rb:mb-2">{t('knowledgeBase.previewFailedDesc')}</p>
                {errorMessage && (
                  <p className="rb:text-sm rb:text-red-600 rb:mb-3">{t('knowledgeBase.errorDetails')}{errorMessage}</p>
                )}
                <p className="rb:text-sm rb:text-gray-600 rb:mb-3">{t('knowledgeBase.possibleReasons')}</p>
                <ul className="rb:list-disc rb:pl-5 rb:text-sm rb:text-gray-600 rb:mb-3">
                  <li>{t('knowledgeBase.fileUrlAccessError')}</li>
                  <li>{t('knowledgeBase.tokenExpired')}</li>
                  <li>{t('knowledgeBase.fileFormatError')}</li>
                  <li>{t('knowledgeBase.networkError')}</li>
                </ul>
                <Flex gap={8} className="rb:mt-4!">
                  <Button icon={<ReloadOutlined />} onClick={handleRetry}>{t('knowledgeBase.retry')}</Button>
                  <Button icon={<DownloadOutlined />} onClick={handleDownload}>{t('knowledgeBase.downloadFile')}</Button>
                </Flex>
              </div>
            }
            type="error"
            showIcon
          />
        </Flex>
      )}

      {/* Image preview */}
      {isImageExt(ext) && !error && !loading && (
        <Flex align="center" justify="center" className="rb:w-full rb:flex-1 rb:overflow-auto rb:bg-gray-50">
          <Image
            src={imageBlobUrl}
            alt={fileName || t('knowledgeBase.imagePreview')}
            style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
            onError={() => handleError(t('knowledgeBase.imageRenderFailed'))}
          />
        </Flex>
      )}

      {/* Markdown preview */}
      {isMarkdownExt(ext) && !error && !loading && (
        <div className="rb:w-full rb:flex-1 rb:overflow-auto rb:bg-white rb:p-6 rb:rounded rb:border rb:border-gray-200">
          <RbMarkdown content={textContent} />
        </div>
      )}

      {/* Text preview - line-by-line virtual scrolling with react-window */}
      {isTextExt(ext) && !error && !loading && (
        <TextVirtualList content={textContent} />
      )}

      {/* Word preview */}
      {isWordExt(ext) && !error && !loading && (
        ext === '.doc' ? (
          /* The legacy .doc format cannot be parsed on the frontend, prompt to download */
          <Flex align="center" justify="center" className="rb:w-full rb:flex-1 rb:bg-gray-50">
            <div className="rb:text-center">
              <p className="rb:text-gray-600 rb:mb-4">{t('knowledgeBase.docFormatNotSupported')}</p>
              <Button icon={<DownloadOutlined />} type="primary" onClick={handleDownload}>{t('knowledgeBase.downloadFile')}</Button>
            </div>
          </Flex>
        ) : (
          <div className="rb:w-full rb:flex-1 rb:overflow-auto rb:bg-white rb:p-6 rb:rounded rb:border rb:border-gray-200">
            <div
              className="rb:prose rb:max-w-none"
              dangerouslySetInnerHTML={{ __html: htmlContent }}
            />
          </div>
        )
      )}

      {/* Excel/CSV preview */}
      {isExcelExt(ext) && !error && !loading && (
        <div className="rb:w-full rb:flex-1 rb:overflow-auto rb:bg-white rb:p-4 rb:rounded rb:border rb:border-gray-200">
          {csvTruncated && (
            <div className="rb:mb-3 rb:px-3 rb:py-2 rb:bg-yellow-50 rb:border rb:border-yellow-200 rb:rounded rb:text-sm rb:text-yellow-700">
              {t('knowledgeBase.fileTooLargePreview', { MAX_PREVIEW_ROWS })}
            </div>
          )}
          {excelData.map((sheet, index) => (
            <div key={index} className="rb:mb-6">
              <h3 className="rb:text-lg rb:font-semibold rb:mb-3">{sheet.sheetName}</h3>
              {sheet.data.length > 0 && (
                <Table
                  dataSource={sheet.data.slice(1).map((row, idx) => ({ key: idx, ...row }))}
                  columns={sheet.data[0]?.map((header: any, colIdx: number) => ({
                    title: header || `${t('knowledgeBase.columnPreview')} ${colIdx + 1}`,
                    dataIndex: colIdx,
                    key: colIdx,
                    width: 150,
                  })) || []}
                  pagination={false}
                  scroll={{ x: 'max-content' }}
                  size="small"
                  bordered
                  virtual
                />
              )}
            </div>
          ))}
        </div>
      )}

      {/* PDF preview - with pagination and zoom */}
      {isPdfExt(ext) && !error && !loading && (
        <>
          <Flex justify="center" className="rb:w-full rb:flex-1 rb:overflow-auto rb:bg-gray-100 rb:p-4!">
            <canvas ref={pdfCanvasRef} className="rb:shadow-lg" />
          </Flex>
          {pdfTotalPages > 0 && (
            <PaginationBar
              currentPage={pdfCurrentPage}
              totalPages={pdfTotalPages}
              onPageChange={handlePdfPageChange}
              extraControls={
                <Flex align="center" gap={4} className="rb:ml-4!">
                  <Button
                    size="small"
                    icon={<ZoomOutOutlined />}
                    disabled={pdfScale <= 0.5}
                    onClick={() => handlePdfZoom(-0.25)}
                  />
                  <span className="rb:text-sm rb:text-gray-600 rb:min-w-12 rb:text-center">
                    {Math.round(pdfScale * 100)}%
                  </span>
                  <Button
                    size="small"
                    icon={<ZoomInOutlined />}
                    disabled={pdfScale >= 3}
                    onClick={() => handlePdfZoom(0.25)}
                  />
                </Flex>
              }
            />
          )}
        </>
      )}

      {/* PPT/PPTX preview */}
      {isPptExt(ext) && !error && !loading && (
        <>
          {pptSlides.length > 0 ? (
            /* Local rendering mode (the server returned a parseable format) */
            <>
              <Flex align="center" justify="center" className="rb:w-full rb:flex-1 rb:overflow-auto rb:bg-gray-100 rb:p-4!">
                <img
                  src={pptSlides[pptCurrentPage - 1]}
                  alt={`Slide ${pptCurrentPage}`}
                  className="rb:max-w-full rb:max-h-full rb:object-contain rb:shadow-lg"
                />
              </Flex>
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
            <Flex vertical className="rb:w-full rb:flex-1">
              <iframe
                src={`https://view.officeapps.live.com/op/embed.aspx?src=${encodeURIComponent(fileUrl)}`}
                width="100%"
                height="100%"
                title={fileName || t('knowledgeBase.pptPreview')}
                className="rb:border-0 rb:flex-1"
                style={{ border: 'none' }}
                onError={() => handleError(t('knowledgeBase.pptPreviewFailed'))}
              />
              <Flex align="center" justify="center" gap={12} className="rb:py-2! rb:px-4! rb:bg-white rb:border-t rb:border-gray-200">
                <span className="rb:text-sm rb:text-gray-500">{t('knowledgeBase.useOfficeOnlinePreview')}</span>
                <Button size="small" icon={<DownloadOutlined />} onClick={handleDownload}>
                  {t('knowledgeBase.downloadFile')}
                </Button>
              </Flex>
            </Flex>
          )}
        </>
      )}
    </Flex>
  );
};

export default DocumentPreview;
