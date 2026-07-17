import { resolve } from 'node:path'

import autoprefixer from 'autoprefixer'
import react from '@vitejs/plugin-react'
import tailwindcss from 'tailwindcss'
import { defineConfig, loadEnv } from 'vite'
import tsconfigPaths from 'vite-tsconfig-paths'

const webRoot = __dirname

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, webRoot, '')
  const devPort = parseInt(env.VITE_DEV_PORT || '3003', 10)
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || 'http://localhost:13000'
  const minioProxyTarget = env.VITE_MINIO_PROXY_TARGET || 'http://localhost:9000'

  return {
    root: webRoot,
    envDir: webRoot,
    cacheDir: resolve(webRoot, '../node_modules/.vite/apps-web'),
    css: {
      postcss: {
        plugins: [
          tailwindcss({
            config: resolve(webRoot, 'tailwind.config.js'),
          }),
          autoprefixer(),
        ],
      },
    },
    server: {
      host: '0.0.0.0',
      port: devPort,
      proxy: {
        '/api': {
          target: apiProxyTarget,
          changeOrigin: true,
        },
        '/minio': {
          target: minioProxyTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/minio/, ''),
        },
      },
    },
    build: {
      outDir: resolve(webRoot, 'dist'),
      sourcemap: false,
      cssCodeSplit: true,
      chunkSizeWarningLimit: 550,
    },
    plugins: [
      react({
        babel: mode === 'development'
          ? {
              plugins: ['react-dev-locator'],
            }
          : undefined,
      }),
      tsconfigPaths({
        projects: [resolve(webRoot, 'tsconfig.json')],
      }),
    ],
  }
})
