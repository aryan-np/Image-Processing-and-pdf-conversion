"""MinIO/S3 stub (optional, not required). Documented for porting only."""
# To enable: pip install boto3, set S3_ENDPOINT/S3_BUCKET/AWS_* env vars,
# and swap core.storage.storage with S3Storage below.
class S3Storage:  # pragma: no cover
    def __init__(self, endpoint=None, bucket="slotlab"):
        self.endpoint, self.bucket = endpoint, bucket
    def save_slot_file(self, *a, **k): raise NotImplementedError("configure boto3 first")
    def save_final_pdf(self, *a, **k): raise NotImplementedError("configure boto3 first")
    def read(self, *a, **k): raise NotImplementedError("configure boto3 first")
    def delete(self, *a, **k): raise NotImplementedError("configure boto3 first")
    def exists(self, *a, **k): return False
