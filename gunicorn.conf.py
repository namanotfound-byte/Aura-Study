"""Gunicorn configuration for running AuraStudy in production (Render).

Local dev never uses this file -- `./run.sh` runs `python -m server.app`,
Flask's dev server, on 127.0.0.1:5055 only. This file is only read when the
app is started with gunicorn, e.g.:

    gunicorn -c gunicorn.conf.py "server.app:create_app()"

which is exactly what render.yaml's startCommand does. Flask's built-in
server is single-threaded, unauthenticated-by-default in its warning banner,
and explicitly documented as unfit for production -- gunicorn is the actual
WSGI server here.

Tuned for Render's free web tier: 512 MB RAM, shared CPU. See SPEC-PHASE3.md
PART C.
"""
import os

# Render injects $PORT and expects the service to bind it on all
# interfaces; 5055 (the local-dev port) is irrelevant here.
bind = "0.0.0.0:{}".format(os.environ.get("PORT", "10000"))

# 2 worker *processes* is the ceiling that's actually sane on a 512 MB
# instance once you account for each worker holding its own Postgres
# connection pool (server/db.py sizes that pool at up to 5 connections per
# process -- 2 workers x 5 = 10 max, comfortably under Neon free tier's
# connection limit). Threads (not more processes) absorb concurrent
# requests within that memory budget -- this workload is I/O-bound
# (Postgres, SMTP, Spotify's API) so threads spend most of their time
# blocked on network I/O, which the GIL releases for.
workers = 2
threads = 4
worker_class = "gthread"

# Generous enough to survive a slow Neon cold start or a sluggish upstream
# Spotify/SMTP call without gunicorn killing the worker mid-request, but
# still bounded so a genuinely hung worker gets recycled rather than
# wedging a slot forever.
timeout = 60
graceful_timeout = 30
keepalive = 5

# Render captures stdout/stderr as the service's log stream -- "-" means
# "write to stdout/stderr" rather than a file gunicorn would need a
# writable, persistent disk for (the free tier's disk is ephemeral).
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Recycle each worker after a while to bound the effect of any slow memory
# growth over the process lifetime (e.g. from long-lived pooled
# connections) -- cheap insurance on a memory-constrained instance. The
# jitter staggers restarts across workers so they don't all recycle in the
# same instant.
max_requests = 500
max_requests_jitter = 50

# IMPORTANT: leave preload_app at its default (False) -- do not set it to
# True. server/db.py's init_db() opens a psycopg_pool.ConnectionPool
# (background maintenance threads + live sockets) at app-factory time.
# preload_app=True would build that pool once in gunicorn's master process
# *before* forking the workers; forking a process that already has open
# sockets and background threads is unsafe (the pool's maintenance threads
# do not survive fork(), and workers could share or corrupt the parent's
# connections). With the default, each worker process imports the app and
# runs create_app() itself, *after* forking, so each worker gets its own
# independent, correctly-initialised pool.
preload_app = False
