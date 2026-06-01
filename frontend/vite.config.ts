import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_URL ?? 'https://65-2-170-26.sslip.io'

  // The API has no CORS headers, so the browser can't call it directly.
  // Proxy /api through the dev/preview server (server-side, no CORS).
  const proxy = {
    '/api': {
      target: apiTarget,
      changeOrigin: true,
      secure: true,
    },
  }

  return {
    plugins: [react()],
    server: { proxy },
    preview: { proxy },
  }
})
