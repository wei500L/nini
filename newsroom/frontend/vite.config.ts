import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import basicSsl from "@vitejs/plugin-basic-ssl";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const httpsEnabled = env.VITE_HTTPS !== "false";
  const configRoot = fileURLToPath(new URL(".", import.meta.url));
  const certificatePath = `${configRoot}localhost+2.pem`;
  const certificateKeyPath = `${configRoot}localhost+2-key.pem`;
  const hasTrustedCertificate =
    existsSync(certificatePath) && existsSync(certificateKeyPath);
  const https = !httpsEnabled
    ? undefined
    : hasTrustedCertificate
      ? {
          cert: readFileSync(certificatePath),
          key: readFileSync(certificateKeyPath),
        }
      : {};
  return {
    plugins: [
      ...(httpsEnabled && !hasTrustedCertificate ? [basicSsl()] : []),
      react(),
      tailwindcss(),
    ],
    server: {
      host: "0.0.0.0",
      https,
      port: 5173,
      proxy: {
        "/api": {
          target: "http://127.0.0.1:8000",
          changeOrigin: true,
        },
        "/health": {
          target: "http://127.0.0.1:8000",
          changeOrigin: true,
        },
      },
    },
    preview: {
      host: "0.0.0.0",
      https,
      port: 4173,
      proxy: {
        "/api": {
          target: "http://127.0.0.1:8000",
          changeOrigin: true,
        },
        "/health": {
          target: "http://127.0.0.1:8000",
          changeOrigin: true,
        },
      },
    },
  };
});
