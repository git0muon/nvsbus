# Compass live tracking on Google Maps -- any Python 3.9+ image works, no dependencies.
FROM python:3.12-slim

WORKDIR /app
COPY compass_tracker.py ./

ENV PYTHONUNBUFFERED=1
# Hosts set $PORT; the app reads it and binds 0.0.0.0.
EXPOSE 5000

CMD ["python", "compass_tracker.py", "--no-browser"]
