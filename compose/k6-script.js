import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter, Gauge } from 'k6/metrics';

// Custom metrics
const errors = new Counter('errors');
const latency = new Trend('latency', { unit: 'ms' });
const successRate = new Rate('success_rate');

const API_URL = __ENV.NETENGINE_TARGET_URL || 'https://api.platform.internal:8080';

export const options = {
  stages: [
    { duration: '30s', target: 5 },   // Ramp up to 5 VUs
    { duration: '2m', target: 20 },   // Ramp up to 20 VUs
    { duration: '2m', target: 20 },   // Stay at 20 VUs
    { duration: '30s', target: 0 },   // Ramp down to 0 VUs
  ],
  thresholds: {
    'http_req_duration': ['p(99)<500'],  // 99th percentile under 500ms
    'http_req_failed': ['rate<0.05'],    // Error rate below 5%
  },
};

export default function () {
  // Health check
  group('Health', () => {
    const res = http.get(`${API_URL}/health`, {
      headers: { 'Content-Type': 'application/json' },
      timeout: '5s',
    });

    const success = check(res, {
      'status is 200': (r) => r.status === 200,
      'response time < 500ms': (r) => r.timings.duration < 500,
    });

    successRate.add(success);
    if (!success) errors.add(1);
    latency.add(res.timings.duration);
  });

  // World status check
  group('World Status', () => {
    const res = http.get(`${API_URL}/world`, {
      headers: { 'Content-Type': 'application/json' },
      timeout: '5s',
    });

    const success = check(res, {
      'status is 200': (r) => r.status === 200,
      'response time < 1000ms': (r) => r.timings.duration < 1000,
      'body has phases': (r) => r.body.includes('phases'),
    });

    successRate.add(success);
    if (!success) errors.add(1);
    latency.add(res.timings.duration);
  });

  // Phase status check
  group('Phase Status', () => {
    for (let i = 0; i < 9; i++) {
      const res = http.get(`${API_URL}/phases/${i}`, {
        headers: { 'Content-Type': 'application/json' },
        timeout: '5s',
      });

      const success = check(res, {
        'status is 200 or 404': (r) => r.status === 200 || r.status === 404,
        'response time < 500ms': (r) => r.timings.duration < 500,
      });

      successRate.add(success);
      if (!success) errors.add(1);
      latency.add(res.timings.duration);
    }
  });

  sleep(1);
}

export function handleSummary(data) {
  // Export results to stdout and Prometheus RW
  console.log('=== Load Test Summary ===');
  console.log(`Total requests: ${data.metrics.http_reqs.value}`);
  console.log(`Failed: ${data.metrics.http_req_failed.value}`);
  console.log(`Success rate: ${data.metrics.success_rate.value * 100}%`);

  return {
    'stdout': textSummary(data, { indent: ' ', enableColors: true }),
  };
}
