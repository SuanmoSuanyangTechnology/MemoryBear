export interface HttpRequestConfigForm {
  method?: string;
  url?: string;
  auth?: {
    auth?: string;
    auth_type?: string;
    header?: string;
    api_key?: string;
  };
  headers?: {
    [key: string]: string;
  };
  params?: {
    [key: string]: string;
  };
  body?: {
    content_type?: string;
    data: string | Record<string, string>;
  };
  verify_ssl?: boolean;
  timeouts?: {
    connect_timeout: number;
    read_timeout: number;
    write_timeout: number;
  };
  retry?: {
    max_attempts: number;
    retry_interval: number;
  };
  error_handle?: {
    method: string;
    default: {
      body: string;
      status_code: number;
      headers: {
        [key: string]: string;
      };
    };
  };
}

export interface AuthConfigModalRef {
  handleOpen: (vo?: HttpRequestConfigForm['auth']) => void;
}