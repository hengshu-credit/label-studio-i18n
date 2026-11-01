module.exports = {
  testDir: './tests/browser', timeout: 60000, retries: 0, workers: 1,
  use: { baseURL: process.env.LABEL_STUDIO_URL || 'http://127.0.0.1:8080', headless: true, trace: 'retain-on-failure' },
  reporter: [['list'], ['html', {outputFolder:'artifacts/playwright-report',open:'never'}]],
};
