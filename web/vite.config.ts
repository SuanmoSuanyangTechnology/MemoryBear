import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'
import { existsSync } from 'fs'
import AutoImport from 'unplugin-auto-import/vite'
import tailwindcss from '@tailwindcss/vite'
import svgr from 'vite-plugin-svgr';

// 处理私有包的虚拟模块插件
const virtualModulePlugin = () => ({
  name: 'virtual-module-plugin',
  enforce: 'pre',
  resolveId(id: string) {
    if (id === '@redbear/memory-brick') {
      const packagePath = resolve(__dirname, 'node_modules/@redbear/memory-brick')

      if (existsSync(packagePath)) {
        // 存在时，使用实际的模块
        const entryFile = resolve(__dirname, 'node_modules/@redbear/memory-brick/dist/index.js')
        console.log('[virtual-module] resolveId:', id, '-> actual package')
        return entryFile
      } else {
        // 不存在时，使用虚拟模块
        console.log('[virtual-module] resolveId:', id, '-> virtual module')
        return '\0@redbear/memory-brick'
      }
    }
    return null
  },
  load(id: string) {
    if (id === '\0@redbear/memory-brick') {
      // 提供虚拟模块的默认导出
      return `
        const FallbackComponent = () => null;
        export const BrainView = FallbackComponent;
        export const Provider = FallbackComponent;
        export default { BrainView, Provider };
      `
    }
    return null
  }
})

// https://vite.dev/config/
export default defineConfig({
  server: {
    host: '0.0.0.0', // 支持通过IP地址访问
    port: 5175,
    proxy: {
      // 主要API代理，支持 /api 和 /api/* 格式
      '/api': {
        target: 'https://devcopymemorybear.redbearai.com/',
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
    virtualModulePlugin(),
    tailwindcss(),
    react(),
    AutoImport({
      imports: ['react', 'react-router-dom'],
      dts: 'public/auto-imports.d.ts',
    }),
    svgr({ svgrOptions: { icon: true } }),
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
      'x6-html-shape': resolve(__dirname, 'src/vendor/x6-html-shape/index.js'),
      'x6-html-shape/dist/react': resolve(__dirname, 'src/vendor/x6-html-shape/react.js'),
      'x6-html-shape/dist/utils.js': resolve(__dirname, 'src/vendor/x6-html-shape/utils.js'),
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
        drop_console: false, // 移除 console
        drop_debugger: true, // 移除 debugger
      },
    },
  },
})