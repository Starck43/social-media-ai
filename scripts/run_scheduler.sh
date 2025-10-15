#!/bin/bash
# Run content collection scheduler

echo "Starting content collection scheduler..."
python -m app.services.scheduler "${1:-60}"
