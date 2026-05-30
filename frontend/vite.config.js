import { resolve } from 'path';
import { defineConfig } from 'vite';

// Multi-page app. COOP/COEP on the dev server give the same cross-origin
// isolation the Spark splat renderer expects (CloudFront sets these in prod).
export default defineConfig({
  server: {
    headers: {
      'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Embedder-Policy': 'require-corp',
    },
  },
  build: {
    rollupOptions: {
      input: {
        index: resolve(__dirname, 'index.html'),
        status: resolve(__dirname, 'status.html'),
        viewer: resolve(__dirname, 'viewer.html'),
        gallery: resolve(__dirname, 'gallery.html'),
      },
    },
  },
});
