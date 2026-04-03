import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'
import AutoImport from 'unplugin-auto-import/vite'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  server: {
    host: '0.0.0.0', // 支持通过IP地址访问
    port: 5175,
    proxy: {
      // 主要API代理，支持 /api 和 /api/* 格式
      '/api': {
        // target: 'http://192.168.110.83:8000', // wxy
        // target: 'http://192.168.110.86:8000', // lxy
        // target: 'http://192.168.110.2:8000', // xjn
        // target: 'http://192.168.110.72:8000', // llq
        // target: 'http://192.168.110.39:8000', // myh
        target: 'https://devmemorybear.redbearai.com/', // 开发后端服务地址
        // target: 'https://devcopymemorybear.redbearai.com/', // 开发sass后端服务地址
        // target: 'https://testmemorybear.redbearai.com/', // 测试后端服务地址
        // target: 'https://memorybear.redbearai.com/', // 预发服务地址
        // target: 'https://cloud.memorybear.ai/', // AMAZON 生产地址
        changeOrigin: true,

        // 匹配所有以/api开头的请求，包括/api/token
        configure: (proxy) => {
          // 确保能够匹配/api/token这样的路径
          proxy.on('error', (err) => {
            console.log('代理错误:', err)
          })
        }
      },
    },
  },
  plugins: [
    tailwindcss(),
    react(),
    AutoImport({
      imports: ['react', 'react-router-dom'],
      dts: 'public/auto-imports.d.ts',
    }),
  ],
  css: {
    modules: {
      generateScopedName: '[name]__[local]___[hash:base64:5]',
      localsConvention: 'camelCaseOnly',
    },
  },
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  base: './', // 使用相对路径，确保资源能正确加载
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    assetsDir: 'assets', // 静态资源目录
    sourcemap: false, // 生产环境不生成 sourcemap
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
      },
      output: {
        // 分块策略
        manualChunks: (id) => {
          if (id.includes('node_modules')) {
            if (id.includes('react') || id.includes('react-dom')) {
              return 'react-vendor';
            }
            if (id.includes('react-router')) {
              return 'router-vendor';
            }
            if (id.includes('antd')) {
              return 'antd-vendor';
            }
            if (id.includes('echarts')) {
              return 'echarts-vendor';
            }
            // 其他第三方库
            return 'vendor';
          }
        },
        // 输出文件命名
        chunkFileNames: 'assets/js/[name]-[hash].js',
        entryFileNames: 'assets/js/[name]-[hash].js',
        assetFileNames: 'assets/[ext]/[name]-[hash].[ext]',
      },
    },
    // 压缩配置
    minify: 'terser',
    terserOptions: {
      compress: {
        drop_console: true, // 移除 console
        drop_debugger: true, // 移除 debugger
      },
    },
  },
})