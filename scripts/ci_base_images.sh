#!/usr/bin/env bash
# Fetch the base images of the stack before a CI job builds or starts it.
#
#   scripts/ci_base_images.sh
#
# Docker Hub limits anonymous downloads for each network address, and hosted CI
# runners share their addresses with many other projects. On 9 October 2026
# three consecutive pipeline runs failed in the first second of the image build
# with "429 Too Many Requests" for python:3.13-slim, although nothing in the
# repository had changed.
#
# Docker publishes its official images on Amazon ECR Public as well
# (public.ecr.aws/docker/library), and Google mirrors them
# (mirror.gcr.io/library). This script tries those two sources first and
# Docker Hub last, three rounds with a pause between them. It gives each image
# the name that the Dockerfile and the Compose file use, so neither file changes
# and the build finds its base image locally. The names come from those two
# files, so a new base image needs no change here.
set -euo pipefail

images="$(sed -n 's/^FROM \([^ ]*\).*/\1/p' Dockerfile
          sed -n 's/^ *image: \(caddy[^ ]*\).*/\1/p' docker-compose.yml)"
for image in $images; do
  fetched=""
  for round in 1 2 3; do
    for source in "public.ecr.aws/docker/library/$image" "mirror.gcr.io/library/$image" "$image"; do
      if docker pull --quiet "$source" > /dev/null 2>&1; then
        [ "$source" = "$image" ] || docker tag "$source" "$image"
        fetched="$source"
        break 2
      fi
    done
    echo "::warning title=Base image::Round $round: no registry delivered $image."
    sleep $((round * 15))
  done
  if [ -z "$fetched" ]; then
    echo "::error title=Base image::$image could not be fetched from any registry."
    exit 1
  fi
  echo "$image from $fetched ($(docker image inspect --format '{{.Id}}' "$image" | cut -c 8-19))"
done
