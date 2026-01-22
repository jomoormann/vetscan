# Gunicorn configuration for VetScan
# Uses uvicorn workers for ASGI support

import os
import multiprocessing

# Bind to port 8000
bind = "0.0.0.0:8000"

# Use uvicorn workers for async support
worker_class = "uvicorn.workers.UvicornWorker"

# Single worker to minimize memory on shared hosting
workers = 1

# Timeout for worker responses (seconds)
timeout = 120

# Graceful timeout for worker restart
graceful_timeout = 30

# Keep-alive connections
keepalive = 5

# Logging
loglevel = "info"
accesslog = "-"  # stdout
errorlog = "-"   # stderr

# Process naming
proc_name = "vetscan"

# Restart workers after this many requests (prevents memory leaks)
max_requests = 1000
max_requests_jitter = 50

# Preload app for faster worker spawning
preload_app = True

# Working directory
chdir = os.path.dirname(os.path.abspath(__file__))

# Python path
pythonpath = "src"
