"""Amazon S3 / MinIO and Google Cloud Storage (GCS) cloud object storage providers."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import logging
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import httpx
from PIL import Image
from pydantic import Field

from recipeboxer.ingestion.scanner import VALID_IMAGE_EXTENSIONS, get_image_mime_type
from recipeboxer.sources.base import (
    BaseSourceProvider,
    ListContainersResponse,
    ListItemsResponse,
    SourceCapabilities,
    SourceContainer,
    SourceItem,
    SourceProviderConfig,
)

logger = logging.getLogger(__name__)


# ============================================================================
# AWS SigV4 Request Signer (Zero-Dependency Pure Python Implementation)
# ============================================================================

def _sign_aws_v4(
    key: bytes, msg: str
) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _get_signature_key(key: str, date_stamp: str, region_name: str, service_name: str) -> bytes:
    k_date = _sign_aws_v4(("AWS4" + key).encode("utf-8"), date_stamp)
    k_region = _sign_aws_v4(k_date, region_name)
    k_service = _sign_aws_v4(k_region, service_name)
    k_signing = _sign_aws_v4(k_service, "aws4_request")
    return k_signing


def sign_s3_request(
    method: str,
    url: str,
    access_key: str,
    secret_key: str,
    region: str = "us-east-1",
    payload: bytes = b"",
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Generate AWS SigV4 authorization headers for S3 REST request."""
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    parsed_url = urllib.parse.urlparse(url)
    host = parsed_url.netloc

    # Canonical URI: normalize and ensure path segments are correctly quoted
    raw_path = parsed_url.path or "/"
    canonical_uri = "/" + "/".join(
        urllib.parse.quote(urllib.parse.unquote(seg), safe="") for seg in raw_path.split("/") if seg
    ) if raw_path != "/" else "/"

    # Canonical Query String: sort by parameter key (and value) per AWS SigV4 specification
    if parsed_url.query:
        query_pairs = urllib.parse.parse_qsl(parsed_url.query, keep_blank_values=True)
        sorted_pairs = sorted(query_pairs, key=lambda kv: (kv[0], kv[1]))
        canonical_querystring = urllib.parse.urlencode(
            sorted_pairs, quote_via=urllib.parse.quote, safe=""
        )
    else:
        canonical_querystring = ""

    req_headers = headers.copy() if headers else {}
    req_headers["host"] = host
    req_headers["x-amz-date"] = amz_date
    payload_hash = hashlib.sha256(payload).hexdigest()
    req_headers["x-amz-content-sha256"] = payload_hash

    # Sorted header keys
    sorted_header_keys = sorted(req_headers.keys())
    canonical_headers = "".join(f"{k.lower()}:{req_headers[k].strip()}\n" for k in sorted_header_keys)
    signed_headers = ";".join(k.lower() for k in sorted_header_keys)

    canonical_request = (
        f"{method}\n"
        f"{canonical_uri}\n"
        f"{canonical_querystring}\n"
        f"{canonical_headers}\n"
        f"{signed_headers}\n"
        f"{payload_hash}"
    )

    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = (
        f"{algorithm}\n"
        f"{amz_date}\n"
        f"{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )

    signing_key = _get_signature_key(secret_key, date_stamp, region, "s3")
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    auth_header = (
        f"{algorithm} "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )
    req_headers["Authorization"] = auth_header
    return req_headers


# ============================================================================
# Amazon S3 / MinIO Provider
# ============================================================================

class S3SourceProvider(BaseSourceProvider):
    """Amazon S3 and S3-compatible (MinIO/LocalStack) object storage provider."""

    provider_id: str = "s3"
    name: str = "Amazon S3 / MinIO"
    description: str = "Batch recipe scan ingestion from S3 buckets with prefix filtering and signed URLs"
    capabilities: SourceCapabilities = SourceCapabilities(
        supports_containers=True,
        supports_search=True,
        supports_thumbnails=True,
        supports_pagination=True,
        supports_streaming=True,
        requires_auth=True,
        supported_mime_types=[
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/tiff",
            "image/bmp",
            "image/heic",
            "image/heif",
            "image/gif",
        ],
        category_filters=[],
    )

    def __init__(
        self,
        config: Optional[SourceProviderConfig] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(config=config)
        self._custom_client = http_client
        self.bucket = self.config.bucket_name or "recipeboxer-scans"
        self.endpoint = (self.config.endpoint_url or f"https://{self.bucket}.s3.amazonaws.com").rstrip("/")
        self.region = self.config.region_name or "us-east-1"
        self.access_key = self.config.access_key_id or ""
        self.secret_key = self.config.secret_access_key or ""
        self._item_cache: Dict[str, SourceItem] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._custom_client is not None:
            return self._custom_client
        return httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))

    def _build_url(self, key: str = "", query: str = "") -> str:
        """Construct REST endpoint URL for object or list request."""
        clean_key = key.lstrip("/").replace("\\", "/")
        encoded_key = "/".join(urllib.parse.quote(part, safe="") for part in clean_key.split("/")) if clean_key else ""
        base = self.endpoint
        # If endpoint does not contain bucket name and isn't path-style:
        if "s3.amazonaws.com" in base and self.bucket not in base:
            base = f"https://{self.bucket}.s3.{self.region}.amazonaws.com"

        url = f"{base}/{encoded_key}" if encoded_key else base
        if query:
            url = f"{url}?{query}" if "?" not in url else f"{url}&{query}"
        return url

    async def _request(
        self,
        method: str,
        key: str = "",
        query: str = "",
        headers: Optional[Dict[str, str]] = None,
        payload: bytes = b"",
    ) -> httpx.Response:
        url = self._build_url(key=key, query=query)
        client = await self._get_client()
        should_close = self._custom_client is None

        req_headers = headers or {}
        if self.access_key and self.secret_key:
            req_headers = sign_s3_request(
                method=method,
                url=url,
                access_key=self.access_key,
                secret_key=self.secret_key,
                region=self.region,
                payload=payload,
                headers=req_headers,
            )

        try:
            resp = await client.request(
                method=method,
                url=url,
                headers=req_headers,
                content=payload if payload else None,
            )
            return resp
        finally:
            if should_close:
                await client.aclose()

    def generate_presigned_url(self, item_id: str, expires_in: int = 3600) -> str:
        """Generate presigned GET URL for S3 item."""
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        credential_scope = f"{date_stamp}/{self.region}/s3/aws4_request"

        clean_key = item_id.lstrip("/")
        base_url = self._build_url(clean_key)

        query_params = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.access_key}/{credential_scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(expires_in),
            "X-Amz-SignedHeaders": "host",
        }

        parsed = urllib.parse.urlparse(base_url)
        host = parsed.netloc
        raw_path = parsed.path or "/"
        canonical_uri = "/" + "/".join(
            urllib.parse.quote(urllib.parse.unquote(seg), safe="") for seg in raw_path.split("/") if seg
        ) if raw_path != "/" else "/"

        canonical_query = urllib.parse.urlencode(
            sorted(query_params.items()), quote_via=urllib.parse.quote, safe=""
        )
        canonical_headers = f"host:{host}\n"
        signed_headers = "host"
        payload_hash = "UNSIGNED-PAYLOAD"

        canonical_request = (
            f"GET\n"
            f"{canonical_uri}\n"
            f"{canonical_query}\n"
            f"{canonical_headers}\n"
            f"{signed_headers}\n"
            f"{payload_hash}"
        )

        string_to_sign = (
            f"AWS4-HMAC-SHA256\n"
            f"{amz_date}\n"
            f"{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
        )

        signing_key = _get_signature_key(self.secret_key, date_stamp, self.region, "s3")
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        return f"{base_url}?{canonical_query}&X-Amz-Signature={signature}"

    async def test_connection(self) -> bool:
        """Verify S3 credentials by checking bucket connectivity."""
        try:
            resp = await self._request("GET", query="list-type=2&max-keys=1")
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.debug(f"S3 test_connection failed: {exc}")
            return False

    async def list_containers(
        self,
        page_size: int = 50,
        page_token: Optional[str] = None,
        parent_id: Optional[str] = None,
        **kwargs: Any,
    ) -> ListContainersResponse:
        """List common prefixes (virtual folders) within the bucket."""
        prefix = parent_id or self.config.prefix or ""
        if prefix and not prefix.endswith("/"):
            prefix += "/"

        query = f"list-type=2&delimiter=/&prefix={urllib.parse.quote(prefix)}&max-keys={page_size}"
        if page_token:
            query += f"&continuation-token={urllib.parse.quote(page_token)}"

        resp = await self._request("GET", query=query)
        if resp.status_code != 200:
            return ListContainersResponse(containers=[], next_page_token=None, total_count=0)

        # Parse XML response
        root = ET.fromstring(resp.text)
        ns = {"s3": root.tag.split("}")[0].strip("{")} if "}" in root.tag else {}

        containers: List[SourceContainer] = []
        prefix_elems = root.findall(".//s3:CommonPrefixes/s3:Prefix" if ns else ".//CommonPrefixes/Prefix", ns)
        for p in prefix_elems:
            folder_prefix = p.text or ""
            folder_name = folder_prefix.rstrip("/").split("/")[-1] or folder_prefix
            containers.append(
                SourceContainer(
                    id=folder_prefix,
                    name=folder_name,
                    container_type="prefix",
                    metadata={"bucket": self.bucket, "prefix": folder_prefix},
                )
            )

        next_token_elem = root.find(".//s3:NextContinuationToken" if ns else ".//NextContinuationToken", ns)
        next_tok = next_token_elem.text if next_token_elem is not None else None

        return ListContainersResponse(
            containers=containers,
            next_page_token=next_tok,
            total_count=len(containers),
        )

    async def list_items(
        self,
        container_id: Optional[str] = None,
        query: Optional[str] = None,
        category: Optional[str] = None,
        page_size: int = 50,
        page_token: Optional[str] = None,
        **kwargs: Any,
    ) -> ListItemsResponse:
        """List image objects from S3 bucket matching prefix or search query."""
        prefix = container_id or self.config.prefix or ""
        query_str = f"list-type=2&prefix={urllib.parse.quote(prefix)}&max-keys={min(page_size, 100)}"
        if page_token:
            query_str += f"&continuation-token={urllib.parse.quote(page_token)}"

        resp = await self._request("GET", query=query_str)
        if resp.status_code != 200:
            return ListItemsResponse(items=[], next_page_token=None, total_count=0)

        root = ET.fromstring(resp.text)
        ns = {"s3": root.tag.split("}")[0].strip("{")} if "}" in root.tag else {}

        items: List[SourceItem] = []
        contents = root.findall(".//s3:Contents" if ns else ".//Contents", ns)
        for c in contents:
            key_elem = c.find("s3:Key" if ns else "Key", ns)
            size_elem = c.find("s3:Size" if ns else "Size", ns)
            etag_elem = c.find("s3:ETag" if ns else "ETag", ns)
            lm_elem = c.find("s3:LastModified" if ns else "LastModified", ns)

            key = key_elem.text if key_elem is not None else ""
            if not key or key.endswith("/"):
                continue

            suffix = Path(key).suffix.lower()
            if suffix not in VALID_IMAGE_EXTENSIONS:
                continue

            name = Path(key).name
            if query and query.lower() not in name.lower():
                continue

            size = int(size_elem.text) if size_elem is not None and size_elem.text else 0
            etag = (etag_elem.text or "").strip('"') if etag_elem is not None else None
            last_mod = lm_elem.text if lm_elem is not None else None

            signed_url = None
            if self.access_key and self.secret_key:
                try:
                    signed_url = self.generate_presigned_url(key)
                except Exception:
                    pass

            mime = get_image_mime_type(suffix)

            s_item = SourceItem(
                id=key,
                name=name,
                mime_type=mime,
                size_bytes=size,
                thumbnail_url=signed_url,
                download_url=signed_url,
                sha256_hash=etag if etag and len(etag) == 64 else None,
                container_id=prefix,
                created_at=last_mod,
                metadata={"bucket": self.bucket, "key": key, "etag": etag},
            )
            items.append(s_item)
            self._item_cache[key] = s_item

        next_token_elem = root.find(".//s3:NextContinuationToken" if ns else ".//NextContinuationToken", ns)
        next_tok = next_token_elem.text if next_token_elem is not None else None

        return ListItemsResponse(
            items=items,
            next_page_token=next_tok,
            total_count=len(items),
        )

    async def get_metadata(self, item_id: str) -> SourceItem:
        """Retrieve S3 object metadata via HEAD request."""
        if item_id in self._item_cache:
            return self._item_cache[item_id]

        resp = await self._request("HEAD", key=item_id)
        if resp.status_code >= 400:
            raise FileNotFoundError(f"S3 object not found: {item_id}")

        size = int(resp.headers.get("content-length", 0))
        mime = resp.headers.get("content-type") or get_image_mime_type(item_id)
        etag = resp.headers.get("etag", "").strip('"')
        last_mod = resp.headers.get("last-modified")

        s_item = SourceItem(
            id=item_id,
            name=Path(item_id).name,
            mime_type=mime,
            size_bytes=size,
            thumbnail_url=self.generate_presigned_url(item_id) if self.access_key else None,
            download_url=self.generate_presigned_url(item_id) if self.access_key else None,
            sha256_hash=etag if len(etag) == 64 else None,
            created_at=last_mod,
            metadata={"bucket": self.bucket, "key": item_id, "etag": etag},
        )
        self._item_cache[item_id] = s_item
        return s_item

    async def fetch_thumbnail(self, item_id: str, max_size: int = 256) -> Optional[bytes]:
        """Fetch image bytes and resize to thumbnail."""
        try:
            data = await self.download_image(item_id)
        except Exception as exc:
            logger.warning(f"Failed downloading S3 object {item_id} for thumbnail: {exc}")
            return None

        def _resize() -> Optional[bytes]:
            try:
                with Image.open(io.BytesIO(data)) as img:
                    img = img.convert("RGB")
                    img.thumbnail((max_size, max_size))
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=85)
                    return buf.getvalue()
            except Exception as exc:
                logger.warning(f"Failed generating thumbnail for S3 item {item_id}: {exc}")
                return None

        return await asyncio.to_thread(_resize)

    async def download_image(self, item_id: str) -> bytes:
        """Download raw binary object from S3."""
        resp = await self._request("GET", key=item_id)
        if resp.status_code >= 400:
            raise FileNotFoundError(f"Failed downloading S3 object {item_id}: HTTP {resp.status_code}")
        return resp.content


# ============================================================================
# Google Cloud Storage (GCS) Provider
# ============================================================================

GCS_API_BASE = "https://storage.googleapis.com/storage/v1"


class GCSSourceProvider(BaseSourceProvider):
    """Google Cloud Storage bucket connector for batch recipe scan ingestion."""

    provider_id: str = "gcs"
    name: str = "Google Cloud Storage"
    description: str = "Batch recipe scan ingestion from Google Cloud Storage buckets"
    capabilities: SourceCapabilities = SourceCapabilities(
        supports_containers=True,
        supports_search=True,
        supports_thumbnails=True,
        supports_pagination=True,
        supports_streaming=True,
        requires_auth=True,
        supported_mime_types=[
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/tiff",
            "image/bmp",
            "image/heic",
            "image/heif",
            "image/gif",
        ],
        category_filters=[],
    )

    def __init__(
        self,
        config: Optional[SourceProviderConfig] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(config=config)
        self._custom_client = http_client
        self.bucket = self.config.bucket_name or "recipeboxer-archive"
        self._token: Optional[str] = self.config.auth_token if self.config else None
        self._item_cache: Dict[str, SourceItem] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._custom_client is not None:
            return self._custom_client
        return httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))

    def generate_download_url(self, item_id: str) -> str:
        """Construct standard direct download URL for GCS object."""
        encoded_id = urllib.parse.quote(item_id, safe="")
        return f"{GCS_API_BASE}/b/{self.bucket}/o/{encoded_id}?alt=media"

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        is_binary: bool = False,
    ) -> httpx.Response:
        url = f"{GCS_API_BASE}/b/{self.bucket}/{path}" if path else f"{GCS_API_BASE}/b/{self.bucket}"
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if not is_binary:
            headers["Accept"] = "application/json"

        client = await self._get_client()
        should_close = self._custom_client is None
        try:
            resp = await client.request(method=method, url=url, headers=headers, params=params)
            return resp
        finally:
            if should_close:
                await client.aclose()

    async def test_connection(self) -> bool:
        """Verify GCS bucket accessibility."""
        try:
            resp = await self._request("GET", "o", params={"maxResults": 1})
            return resp.status_code == 200
        except Exception as exc:
            logger.debug(f"GCS test_connection failed: {exc}")
            return False

    async def list_containers(
        self,
        page_size: int = 50,
        page_token: Optional[str] = None,
        parent_id: Optional[str] = None,
        **kwargs: Any,
    ) -> ListContainersResponse:
        """List prefixes (directories) in GCS bucket."""
        prefix = parent_id or self.config.prefix or ""
        params: Dict[str, Any] = {
            "delimiter": "/",
            "maxResults": min(page_size, 100),
        }
        if prefix:
            params["prefix"] = prefix if prefix.endswith("/") else f"{prefix}/"
        if page_token:
            params["pageToken"] = page_token

        resp = await self._request("GET", "o", params=params)
        if resp.status_code != 200:
            return ListContainersResponse(containers=[], next_page_token=None, total_count=0)

        data = resp.json()
        prefixes = data.get("prefixes", [])
        containers: List[SourceContainer] = []
        for p in prefixes:
            folder_name = p.rstrip("/").split("/")[-1] or p
            containers.append(
                SourceContainer(
                    id=p,
                    name=folder_name,
                    container_type="prefix",
                    metadata={"bucket": self.bucket, "prefix": p},
                )
            )

        return ListContainersResponse(
            containers=containers,
            next_page_token=data.get("nextPageToken"),
            total_count=len(containers),
        )

    async def list_items(
        self,
        container_id: Optional[str] = None,
        query: Optional[str] = None,
        category: Optional[str] = None,
        page_size: int = 50,
        page_token: Optional[str] = None,
        **kwargs: Any,
    ) -> ListItemsResponse:
        """List objects in GCS bucket matching image extensions."""
        prefix = container_id or self.config.prefix or ""
        params: Dict[str, Any] = {
            "maxResults": min(page_size, 100),
        }
        if prefix:
            params["prefix"] = prefix
        if page_token:
            params["pageToken"] = page_token

        resp = await self._request("GET", "o", params=params)
        if resp.status_code != 200:
            return ListItemsResponse(items=[], next_page_token=None, total_count=0)

        data = resp.json()
        raw_items = data.get("items", [])
        items: List[SourceItem] = []

        for obj in raw_items:
            name = obj.get("name", "")
            if not name or name.endswith("/"):
                continue

            suffix = Path(name).suffix.lower()
            if suffix not in VALID_IMAGE_EXTENSIONS:
                continue

            if query and query.lower() not in Path(name).name.lower():
                continue

            size = int(obj.get("size", 0))
            mime = obj.get("contentType") or get_image_mime_type(suffix)
            media_link = obj.get("mediaLink") or self.generate_download_url(name)
            created_at = obj.get("timeCreated")
            md5_hash = obj.get("md5Hash")

            s_item = SourceItem(
                id=name,
                name=Path(name).name,
                mime_type=mime,
                size_bytes=size,
                thumbnail_url=media_link,
                download_url=media_link,
                container_id=prefix,
                created_at=created_at,
                metadata={"bucket": self.bucket, "name": name, "md5Hash": md5_hash},
            )
            items.append(s_item)
            self._item_cache[name] = s_item

        return ListItemsResponse(
            items=items,
            next_page_token=data.get("nextPageToken"),
            total_count=len(items),
        )

    async def get_metadata(self, item_id: str) -> SourceItem:
        """Retrieve object metadata from GCS."""
        if item_id in self._item_cache:
            return self._item_cache[item_id]

        encoded_id = urllib.parse.quote(item_id, safe="")
        resp = await self._request("GET", f"o/{encoded_id}")
        if resp.status_code >= 400:
            raise FileNotFoundError(f"GCS object not found: {item_id}")

        obj = resp.json()
        name = obj.get("name", item_id)
        suffix = Path(name).suffix.lower()
        mime = obj.get("contentType") or get_image_mime_type(suffix)
        media_link = obj.get("mediaLink") or self.generate_download_url(name)

        s_item = SourceItem(
            id=name,
            name=Path(name).name,
            mime_type=mime,
            size_bytes=int(obj.get("size", 0)),
            download_url=media_link,
            thumbnail_url=media_link,
            created_at=obj.get("timeCreated"),
            metadata=obj,
        )
        self._item_cache[item_id] = s_item
        return s_item

    async def fetch_thumbnail(self, item_id: str, max_size: int = 256) -> Optional[bytes]:
        """Fetch image bytes and resize to thumbnail."""
        try:
            data = await self.download_image(item_id)
        except Exception as exc:
            logger.warning(f"Failed downloading GCS object {item_id} for thumbnail: {exc}")
            return None

        def _resize() -> Optional[bytes]:
            try:
                with Image.open(io.BytesIO(data)) as img:
                    img = img.convert("RGB")
                    img.thumbnail((max_size, max_size))
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=85)
                    return buf.getvalue()
            except Exception as exc:
                logger.warning(f"Failed generating thumbnail for GCS item {item_id}: {exc}")
                return None

        return await asyncio.to_thread(_resize)

    async def download_image(self, item_id: str) -> bytes:
        """Download raw binary object from GCS."""
        encoded_id = urllib.parse.quote(item_id, safe="")
        resp = await self._request("GET", f"o/{encoded_id}", params={"alt": "media"}, is_binary=True)
        if resp.status_code >= 400:
            raise FileNotFoundError(f"Failed downloading GCS object {item_id}: HTTP {resp.status_code}")
        return resp.content
