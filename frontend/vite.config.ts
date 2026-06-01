import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // In production the app and API share an origin via Caddy. For local dev/preview
  // the app calls the relative /api path, so proxy it to a reachable backend.
  const apiTarget = env.VITE_API_URL ?? 'https://65-2-170-26.sslip.io'

  const proxy = {
    '/api': { target: apiTarget, changeOrigin: true, secure: true },
  }

  return {
    plugins: [react()],
    server: { proxy },
    preview: { proxy },
  }
})
