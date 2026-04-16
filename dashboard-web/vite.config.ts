import {defineConfig, loadEnv} from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({mode}) => {
  const env = loadEnv(mode, process.cwd(), '')
  const proxyTarget = String(env.VITE_DEV_API_PROXY_TARGET || 'http://127.0.0.1:8765').trim()

  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: false
        }
      }
    }
  }
})
