/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-02 13:46:53
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-02 14:38:33
 */
/**
 * Form validation utilities
 */

interface UploadFile {
  originFileObj: Blob;
  [key: string]: unknown;
}

/**
 * Validate if uploaded image is square (width === height)
 * @param errorMessage - Error message to display when validation fails
 * @returns Ant Design form validator
 */
export const validateSquareImage = (errorMessage: string = 'Image must be square') => {
  return (_: unknown, value: UploadFile | UploadFile[] | undefined) => {
    if (!value || (Array.isArray(value) && value.length === 0)) {
      return Promise.resolve();
    }
    
    const file = Array.isArray(value) ? value[0] : value;
    
    if (file?.originFileObj) {
      return new Promise<void>((resolve, reject) => {
        const img = new Image();
        img.onload = () => {
          if (img.width === img.height) {
            resolve();
          } else {
            reject(new Error(errorMessage));
          }
        };
        img.onerror = () => reject(new Error('Failed to load image'));
        img.src = URL.createObjectURL(file.originFileObj);
      });
    }
    
    return Promise.resolve();
  };
};

// - Cannot be empty or pure whitespace
// - Cannot start with a space
export const stringRegExp = /^[a-zA-Z0-9\u4e00-\u9fa5][a-zA-Z0-9\u4e00-\u9fa5\s]*$/