import http from 'k6/http';
import { sleep, check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.SITE_API_KEY || '';

export const options = {
  stages: [
    { duration: '30s', target: 3 },
    { duration: '1m', target: 3 },
    { duration: '10s', target: 0 },
  ],
  thresholds: {
    http_req_duration: [
      'p(90)<500',
      'p(95)<750',
      'p(99)<1500',
    ],
    http_req_failed: ['rate<0.01'],
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

  check(res, {
    'status is 200': (r) => r.status === 200,
    'response time < 1500ms': (r) => r.timings.duration < 1500,
  });

  sleep(Math.random() * 3 + 0.5);
}
