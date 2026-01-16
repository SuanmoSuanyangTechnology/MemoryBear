import { cookieUtils } from './request'
export const clearAuthData = () => {
  console.log("Clearing auth data and redirecting to login");
  localStorage.removeItem('user')
  localStorage.removeItem('breadcrumbs')
  cookieUtils.clear();
}