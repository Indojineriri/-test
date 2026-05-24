import datetime


def upload_bytes(
    data: bytes,
    filename: str,
    content_type: str,
    bucket_name: str,
    prefix: str = "",
) -> str:
    """Upload bytes to GCS and return the gs:// URI.

    Raises if credentials or the bucket are not available so the caller can
    surface the problem to the user.
    """
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    blob_name = f"{prefix}/{ts}/{filename}" if prefix else f"{ts}/{filename}"
    blob = bucket.blob(blob_name)
    blob.upload_from_string(data, content_type=content_type)
    return f"gs://{bucket_name}/{blob_name}"
