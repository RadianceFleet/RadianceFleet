import http from 'k6/http';
import { sleep, check } from 'k6';
import { Rate } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.SITE_API_KEY || '';

const errorRate5xx = new Rate('http_5xx_rate');

export const options = {
  stages: [
    { duration: '10s', target: 30 },
    { duration: '30s', target: 30 },
    { duration: '10s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<3000'],
    http_5xx_rate: ['rate<0.05'],
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

export default function () {
  const headers = getHeaders();
  const endpoint = endpoints[Math.floor(Math.random() * endpoints.length)];
  const url = `${BASE_URL}${endpoint}`;

  const res = http.get(url, { headers });

  const is5xx = res.status >= 500 && res.status < 600;
  errorRate5xx.add(is5xx);

  check(res, {
    'status is not 5xx': (r) => r.status < 500,
    'response time < 3000ms': (r) => r.timings.duration < 3000,
  });

  if (is5xx) {
    console.warn(
      `[spike] 5xx response: endpoint=${endpoint} status=${res.status} duration=${res.timings.duration.toFixed(1)}ms`
    );
  }

  sleep(Math.random() * 3 + 0.5);
}
