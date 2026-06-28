#!/usr/bin/env python3
"""
Custom Prometheus metrics exporter for NetEngine.
Exposes metrics about world status, phase completion, domain counts, etc.
"""

import os
import time
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
import requests

NETENGINE_API_URL = os.getenv('NETENGINE_API_URL', 'http://netengine_api:8080')
METRICS_PORT = int(os.getenv('METRICS_PORT', '9555'))

# Registry for metrics
registry = CollectorRegistry()

# Define metrics
world_status = Gauge('netengine_world_status', 'World deployment status (1=running)', registry=registry)
phases_completed = Gauge('netengine_phases_completed', 'Number of completed phases', registry=registry)
domains_registered = Gauge('netengine_domains_registered', 'Total domains registered', registry=registry)
orgs_deployed = Gauge('netengine_orgs_deployed', 'Total organizations deployed', registry=registry)
ands_count = Gauge('netengine_ands_count', 'Total Administrative Network Domains', registry=registry)

api_requests = Counter('netengine_api_requests_total', 'Total API requests', ['endpoint', 'status'], registry=registry)
api_latency = Histogram('netengine_api_latency_seconds', 'API request latency', ['endpoint'], registry=registry)

class MetricsHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Prometheus metrics endpoint."""

    def do_GET(self):
        """Handle GET requests."""
        if self.path == '/metrics':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(generate_latest(registry))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


def collect_netengine_metrics():
    """Fetch NetEngine metrics from the API and update Prometheus metrics."""
    try:
        # Get world status
        start = time.time()
        response = requests.get(f'{NETENGINE_API_URL}/world', timeout=5)
        latency = time.time() - start

        api_requests.labels(endpoint='/world', status=response.status_code).inc()
        api_latency.labels(endpoint='/world').observe(latency)

        if response.status_code == 200:
            world_data = response.json()
            world_status.set(1)  # World is running

            # Count completed phases
            phases = world_data.get('phases', [])
            completed = sum(1 for p in phases if p.get('status') == 'completed')
            phases_completed.set(completed)

            # Extract domain and org counts if available
            metadata = world_data.get('metadata', {})
            orgs = metadata.get('organizations', [])
            orgs_deployed.set(len(orgs))

            ands = metadata.get('ands', [])
            ands_count.set(len(ands))

        else:
            world_status.set(0)  # World is not responding

    except requests.exceptions.RequestException as e:
        print(f"Error fetching metrics: {e}")
        world_status.set(0)
        api_requests.labels(endpoint='/world', status='error').inc()


def main():
    """Start metrics collection and HTTP server."""
    print(f"NetEngine Metrics Exporter")
    print(f"API URL: {NETENGINE_API_URL}")
    print(f"Metrics port: {METRICS_PORT}")
    print(f"Endpoint: http://0.0.0.0:{METRICS_PORT}/metrics")

    # Start background metrics collection
    def collect_loop():
        while True:
            try:
                collect_netengine_metrics()
            except Exception as e:
                print(f"Metrics collection error: {e}")
            time.sleep(15)  # Collect every 15 seconds

    import threading
    collector_thread = threading.Thread(target=collect_loop, daemon=True)
    collector_thread.start()

    # Start HTTP server
    server = HTTPServer(('0.0.0.0', METRICS_PORT), MetricsHandler)
    print(f"Starting server on port {METRICS_PORT}...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down...")
        server.shutdown()


if __name__ == '__main__':
    main()
