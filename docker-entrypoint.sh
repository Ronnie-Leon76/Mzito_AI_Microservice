#!/bin/sh
set -e

missing=""
for var in WEBHOOK_SHARED_SECRET FRESHSERVICE_DOMAIN FRESHSERVICE_API_KEY; do
  eval "val=\${$var}"
  if [ -z "$val" ]; then
    missing="${missing}  - ${var}\n"
  fi
done

if [ -n "$missing" ]; then
  echo "ERROR: Required environment variables are not set:"
  printf "%b" "$missing"
  echo ""
  echo "The Docker image does not bundle .env. Configure these in your deployment platform:"
  echo "  - Docker Compose: place a .env file beside docker-compose.yml (see .env.example)"
  echo "  - Azure App Service / Container Apps: Application Settings / Environment variables"
  echo "  - Kubernetes: mount a Secret or set env on the Deployment"
  echo "  - docker run: pass -e WEBHOOK_SHARED_SECRET=... -e FRESHSERVICE_DOMAIN=... -e FRESHSERVICE_API_KEY=..."
  exit 1
fi

exec "$@"
