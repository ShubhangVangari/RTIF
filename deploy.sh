#!/bin/bash
set -e
export PROJECT=$(gcloud config get-value project)
export SA=wiki-rag-sa@$PROJECT.iam.gserviceaccount.com
export BUCKET=$(gsutil ls | grep wiki-rag-store | sed 's|gs://||; s|/||')
gcloud run deploy wiki-rag \
  --source . \
  --region us-central1 \
  --service-account=$SA \
  --set-env-vars BUCKET=$BUCKET,GOOGLE_CLOUD_PROJECT=$PROJECT \
  --allow-unauthenticated
