import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const apiTarget = process.env.FUTURECUP_API_TARGET || 'http://127.0.0.1:8000'
const target = new URL(apiTarget)
if (
  target.protocol !== 'http:' ||
  target.hostname !== '127.0.0.1' ||
  target.username ||
  target.password ||
  target.search ||
  target.hash ||
  target.pathname !== '/'
) {
  throw new Error('FUTURECUP_API_TARGET must be a loopback HTTP origin')
}

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: { '/api': { target: apiTarget } },
  },
})
