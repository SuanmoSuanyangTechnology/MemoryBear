export const clearAuthData = () => {
  console.log("Clearing auth data and redirecting to login");
  sessionStorage.clear();
  localStorage.clear()
}