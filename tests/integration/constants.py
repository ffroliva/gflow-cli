# Pinned Docker image tags — keep in sync with docker-compose.yml.
# Update both files together when upgrading the local dev environment.
MINIO_IMAGE: str = "minio/minio:RELEASE.2024-06-13T22-53-53Z"
FAKE_GCS_IMAGE: str = "fsouza/fake-gcs-server:1.49.3"

# Credentials — local dev / CI only; never use in production.
MINIO_ACCESS_KEY: str = "minioadmin"
MINIO_SECRET_KEY: str = "minioadmin"  # noqa: S105

# Bucket / prefix used for integration-test objects.
MINIO_BUCKET: str = "gflow-test"
GCS_BUCKET: str = "gflow-test"
STORAGE_PREFIX: str = "gflow/"
