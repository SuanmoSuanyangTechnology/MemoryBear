/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:34:23 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 16:34:23 
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
