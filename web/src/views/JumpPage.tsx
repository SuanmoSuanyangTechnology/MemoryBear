/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-04 18:34:36 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-04 18:49:59
 */
import { useEffect, type FC } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { cookieUtils } from '@/utils/request'

/**
 * JumpPage Component
 * 
 * This is an intermediate redirect page used for OAuth authentication flow.
 * It handles the callback from external authentication providers by:
 * 1. Extracting authentication tokens from URL query parameters
 * 2. Storing tokens in cookies for subsequent API requests
 * 3. Redirecting users to their intended destination
 * 
 * Expected URL format:
 * /jump?access_token=xxx&refresh_token=yyy&target=/dashboard
 * 
 * @returns null - This component doesn't render any UI, it only handles side effects
 */
const JumpPage: FC = () => {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  useEffect(() => {
    // Convert URLSearchParams to a plain object for easier access
    const data = Object.fromEntries(searchParams)
    const { access_token, refresh_token, target } = data

    // Store authentication tokens in cookies for API authorization
    cookieUtils.set('authToken', access_token)
    cookieUtils.set('refreshToken', refresh_token)

    // Redirect to the target page if specified
    if (target) {
      // Use setTimeout to ensure cookie operations complete before navigation
      setTimeout(() => {
        // Replace current history entry to prevent users from going back to this page
        navigate(target, { replace: true })
      }, 0)
    }
  }, [searchParams, navigate])
  
  // No UI rendering needed - this is a pure redirect handler
  return null
}

export default JumpPage