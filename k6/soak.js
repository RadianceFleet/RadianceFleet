import http from 'k6/http';
import { sleep, check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.SITE_API_KEY || '';

export const options = {
  stages: [
    { duration: '5m', target: 10 },
    { duration: '1h50m', target: 10 },
    { duration: '5m', target: 0 },
  ],
  thresholds: {
    http_req_duration: ['p(99)<2000'],
    http_req_failed: ['rate<0.02'],
  },
};

const endpoints = [
  '/health',
  '/api/v1/vessels?limit=5',
  '/api/v1/alerts?limit=5',
  '/api/v1/corridors',
  '/',
];

function getHeaders() {
  const headers = {};
  if (API_KEY) {
    headers['X-API-Key'] = API_KEY;
  }
  return headers;
}

let lastLogTime = 0;
const LOG_INTERVAL = 60; // log latency every 60 seconds

export default function () {
  const headers = getHeaders();
  const endpoint = endpoints[Math.floor(Math.random() * endpoints.length)];
  const url = `${BASE_URL}${endpoint}`;

  const res = http.get(url, { headers });

  check(res, {
    'status is 200': (r) => r.status === 200,
    'response time < 2000ms': (r) => r.timings.duration < 2000,
  });

  // Periodically log latency to monitor drift
  const now = Math.floor(Date.now() / 1000);
  if (now - lastLogTime >= LOG_INTERVAL) {
    lastLogTime = now;
    console.log(
      `[soak] endpoint=${endpoint} duration=${res.timings.duration.toFixed(1)}ms status=${res.status}`
    );
  }

  sleep(Math.random() * 3 + 0.5);
}
