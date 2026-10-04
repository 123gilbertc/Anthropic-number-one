import { defineConfig, devices } from "@playwright/test";

// The backend serves the built dashboard. Run `npm run build` first.
// Uses an unreachable DATABASE_URL so e2e runs never write to a developer database.
const PORT = 8765;
export default defineConfig({
  testDir: "e2e",
  timeout: 180_000,
  workers: 1,
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "retain-on-failure" },
  webServer: {
    command: `cd .. && SPORTS_EDGE_WORKERS=0 SPORTS_EDGE_API_TOKEN=e2e-token DATABASE_URL=postgresql+psycopg://x:x@127.0.0.1:1/x uv run uvicorn sports_edge.api.app:app_factory --factory --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    timeout: 120_000,
    reuseExistingServer: false,
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], launchOptions: { executablePath: process.env.PW_CHROMIUM } } },
    { name: "mobile", use: { ...devices["Pixel 7"], launchOptions: { executablePath: process.env.PW_CHROMIUM } } },
  ],
});
