import mimetypes
import re
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction


MAX_WIDTH = 2400
MAX_HEIGHT = 1600
QUALITY = 82


class R2Storage:
    """Small S3-compatible client for Cloudflare R2."""

    def __init__(self, client=None, bucket_name=None):
        self.bucket = bucket_name or settings.R2_BUCKET_NAME
        if not self.bucket:
            raise ImproperlyConfigured("R2_BUCKET_NAME is required.")
        self.client = client or self._build_client()

    @staticmethod
    def _build_client():
        import boto3
        from botocore.config import Config

        endpoint_url = settings.R2_ENDPOINT_URL
        if not endpoint_url:
            raise ImproperlyConfigured(
                "R2_ENDPOINT_URL or R2_ACCOUNT_ID is required."
            )
        if not settings.R2_ACCESS_KEY_ID or not settings.R2_SECRET_ACCESS_KEY:
            raise ImproperlyConfigured(
                "R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY are required."
            )

        return boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=settings.R2_REGION_NAME,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            config=Config(signature_version="s3v4"),
        )

    def upload(self, file_obj, *, key, content_type=None, cache_control=None):
        extra_args = {
            "ContentType": content_type
            or mimetypes.guess_type(key)[0]
            or "application/octet-stream",
        }
        if cache_control:
            extra_args["CacheControl"] = cache_control
        file_obj.seek(0)
        self.client.upload_fileobj(
            file_obj,
            self.bucket,
            key,
            ExtraArgs=extra_args,
        )

    def download(self, key, file_obj):
        self.client.download_fileobj(self.bucket, key, file_obj)
        file_obj.seek(0)

    def private_url(self, key, download_file_name=None):
        if not key:
            return None
        params = {"Bucket": self.bucket, "Key": key}
        if download_file_name:
            safe_name = _safe_disposition_filename(download_file_name)
            if safe_name:
                params["ResponseContentDisposition"] = (
                    f'attachment; filename="{safe_name}"'
                )
        return self.client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=settings.R2_PRESIGNED_URL_TTL,
        )

    def public_url(self, key):
        base_url = settings.R2_PUBLIC_URL.rstrip("/")
        if not base_url:
            raise ImproperlyConfigured(
                "R2_PUBLIC_URL is required for publicly served assets."
            )
        return f"{base_url}/{quote(key, safe='/')}"

    def delete(self, key):
        if key:
            self.client.delete_object(Bucket=self.bucket, Key=key)


def upload_image(file_obj, folder=None, public_id=None, *, storage=None):
    """Optimize an image, upload it to R2, and return its permanent URL/key."""

    prepared_file = _prepare_upload_file(file_obj)
    suffix = Path(getattr(prepared_file, "name", "image")).suffix.lower()
    suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else ""
    object_name = _safe_object_name(public_id or uuid4().hex)
    if suffix and not object_name.lower().endswith(suffix):
        object_name = f"{object_name}{suffix}"

    upload_folder = (folder or settings.R2_IMAGES_PREFIX).strip("/")
    key = f"{upload_folder}/{object_name}" if upload_folder else object_name
    storage = storage or R2Storage()
    storage.upload(
        prepared_file,
        key=key,
        content_type=mimetypes.guess_type(key)[0] or "application/octet-stream",
        cache_control=settings.R2_IMAGE_CACHE_CONTROL or None,
    )
    return {"url": storage.public_url(key), "key": key, "public_id": key}


def upload_file(file_obj, folder=None, public_id=None, *, storage=None):
    """Upload a file to R2 unchanged and return its permanent URL/key."""

    suffix = Path(getattr(file_obj, "name", "file")).suffix.lower()
    suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else ""
    object_name = _safe_object_name(public_id or uuid4().hex)
    if suffix and not object_name.lower().endswith(suffix):
        object_name = f"{object_name}{suffix}"

    upload_folder = (folder or settings.R2_FILES_PREFIX).strip("/")
    key = f"{upload_folder}/{object_name}" if upload_folder else object_name
    content_type = (
        getattr(file_obj, "content_type", None)
        or mimetypes.guess_type(key)[0]
        or "application/octet-stream"
    )
    storage = storage or R2Storage()
    storage.upload(file_obj, key=key, content_type=content_type)
    return {
        "url": storage.public_url(key),
        "key": key,
        "public_id": key,
        "content_type": content_type,
    }


def delete_image(public_id=None, image_url=None, *, storage=None):
    """Delete an R2 image by object key or by one of this app's public URLs."""

    key = public_id or extract_key(image_url)
    if not key:
        return {"result": "skipped"}

    (storage or R2Storage()).delete(key)
    return {"result": "deleted", "key": key, "public_id": key}


def schedule_delete_image(public_id=None, image_url=None):
    """Delete an image after the surrounding database transaction commits."""

    key = public_id or extract_key(image_url)
    if not key:
        return {"result": "skipped"}
    transaction.on_commit(lambda: delete_image(public_id=key))
    return {"result": "scheduled", "key": key}


def extract_key(file_url):
    """Return an object key only when the URL belongs to R2_PUBLIC_URL."""

    if not file_url or not settings.R2_PUBLIC_URL:
        return None

    parsed_url = urlparse(file_url)
    parsed_base = urlparse(settings.R2_PUBLIC_URL.rstrip("/"))
    if (
        parsed_url.scheme.casefold() != parsed_base.scheme.casefold()
        or parsed_url.netloc.casefold() != parsed_base.netloc.casefold()
    ):
        return None

    base_path = parsed_base.path.rstrip("/")
    url_path = parsed_url.path
    if base_path and url_path != base_path and not url_path.startswith(f"{base_path}/"):
        return None

    key = url_path[len(base_path) :].lstrip("/")
    return unquote(key) or None


def _safe_disposition_filename(value):
    cleaned = "".join(
        character
        for character in str(value)
        if character.isprintable() and character not in '"\\'
    ).strip()
    return cleaned[:255]


def _safe_object_name(value):
    name = Path(str(value)).name
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-") or uuid4().hex


def _prepare_upload_file(file_obj):
    try:
        original_position = file_obj.tell()
    except (AttributeError, OSError):
        original_position = None

    try:
        file_obj.seek(0)
        original_bytes = file_obj.read()
    except (AttributeError, OSError):
        return file_obj
    finally:
        if original_position is not None:
            file_obj.seek(original_position)

    if not original_bytes:
        return file_obj

    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        return _bytes_upload_file(original_bytes, getattr(file_obj, "name", "image"))

    try:
        image = Image.open(BytesIO(original_bytes))
        if getattr(image, "is_animated", False):
            return _bytes_upload_file(original_bytes, getattr(file_obj, "name", "image"))
        image = ImageOps.exif_transpose(image)
        image.load()
    except (UnidentifiedImageError, OSError):
        return _bytes_upload_file(original_bytes, getattr(file_obj, "name", "image"))

    original_size = image.size
    image.thumbnail((MAX_WIDTH, MAX_HEIGHT), Image.Resampling.LANCZOS)
    resized = image.size != original_size

    candidates = []
    webp_bytes = _encode_image(image, "WEBP", quality=QUALITY, method=6)
    if webp_bytes:
        candidates.append(("image.webp", webp_bytes))

    if image.mode not in ("RGBA", "LA", "P"):
        jpeg_bytes = _encode_image(
            image.convert("RGB"),
            "JPEG",
            quality=QUALITY,
            optimize=True,
            progressive=True,
        )
        if jpeg_bytes:
            candidates.append(("image.jpg", jpeg_bytes))

    if not candidates:
        return _bytes_upload_file(original_bytes, getattr(file_obj, "name", "image"))

    candidate_name, candidate_bytes = min(candidates, key=lambda item: len(item[1]))
    if not resized and len(candidate_bytes) >= len(original_bytes):
        return _bytes_upload_file(original_bytes, getattr(file_obj, "name", "image"))
    return _bytes_upload_file(candidate_bytes, candidate_name)


def _encode_image(image, image_format, **options):
    output = BytesIO()
    try:
        image.save(output, format=image_format, **options)
    except OSError:
        return None
    return output.getvalue()


def _bytes_upload_file(content, name):
    upload_file = BytesIO(content)
    upload_file.name = name
    upload_file.seek(0)
    return upload_file
