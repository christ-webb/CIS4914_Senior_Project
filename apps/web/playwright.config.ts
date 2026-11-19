import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/browser",
  outputDir: process.env.NM5_BROWSER_OUTPUT ?? "/tmp/nm5-browser-results",
  use: {
    baseURL: process.env.NM5_PREVIEW_URL ?? "http://127.0.0.1:3001",
    launchOptions: { executablePath: process.env.NM5_CHROME_PATH },
  },
  workers: 1,
});
