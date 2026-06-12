/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:34:23 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-11 11:41:31
 */
/**
 * Common Utility Functions
 * 
 * Provides general-purpose utility functions.
 * 
 * @module common
 */

/**
 * Generate a random string with specified length and character types
 * @param length - Length of the string (default: 12)
 * @param isHasSpecialChars - Whether to include special characters (default: true)
 * @returns Random string
 */
export const randomString = (length: number = 12, isHasSpecialChars: boolean = true) => {
    /** Define character sets: uppercase, lowercase, numbers, and special characters */
    const uppercaseChars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
    const lowercaseChars = 'abcdefghijklmnopqrstuvwxyz';
    const numberChars = '0123456789';
    const specialChars = '!@#$%^&*_+-=|;:,.?';
    
    /** Combine all character sets */
    let allChars = uppercaseChars + lowercaseChars + numberChars;
    
    /** Ensure at least one character of each type */
    let str = 
      uppercaseChars[Math.floor(Math.random() * uppercaseChars.length)] +
      lowercaseChars[Math.floor(Math.random() * lowercaseChars.length)] +
      numberChars[Math.floor(Math.random() * numberChars.length)]
    if (isHasSpecialChars) {
      allChars+= specialChars;
      str+= specialChars[Math.floor(Math.random() * specialChars.length)];
    }
    
    /** Fill remaining characters to reach desired length */
    for (let i = 4; i < length; i++) {
      str += allChars[Math.floor(Math.random() * allChars.length)];
    }
    
    /** Shuffle the string characters */
    return str.split('').sort(() => Math.random() - 0.5).join('');
}

export const updateMetaIcon = (icon: string) => {
  let link = document.querySelector("link[rel*='icon']") as HTMLLinkElement;
  if (!link) {
    link = document.createElement('link');
    link.rel = 'icon';
    document.head.appendChild(link);
  }
  link.href = icon;


  let meta = document.querySelector("meta[property='og:image']") as HTMLMetaElement;
  if (!meta) {
    meta = document.createElement('meta');
    meta.name = 'og:image';
    document.head.appendChild(meta);
  }
  meta.content = icon;
}
