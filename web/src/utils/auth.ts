/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:34:12 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 16:34:12 
 */
/**
 * Authentication Utility
 * 
 * Provides functions to clear authentication data and redirect to login.
 * 
 * @module auth
 */

import { cookieUtils } from './request'

/**
 * Clear all authentication data and cookies
 * Removes user info, breadcrumbs, and all cookies
 */
export const clearAuthData = () => {
  console.log("Clearing auth data and redirecting to login");
  localStorage.removeItem('user')
  localStorage.removeItem('breadcrumbs')
  cookieUtils.clear();
}
