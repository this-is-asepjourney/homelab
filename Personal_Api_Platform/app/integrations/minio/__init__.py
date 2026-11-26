"""Pure-Python AWS Signature V4 adapter for Homelab MinIO / Silo S3 Object Storage."""

from datetime import datetime, timezone
import hashlib
import hmac
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode, urlparse
import xml.etree.ElementTree as ET

import httpx

from app.core.config import get_settings


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret_key: str, date_stamp: str, region: str, service: str = "s3") -> bytes:
    k_date = _hmac_sha256(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    return _hmac_sha256(k_service, "aws4_request")


class MinioS3Adapter:
    """Lightweight AWS4-HMAC-SHA256 client for replicating Homelab backups to MinIO (:9000)."""

    def __init__(
        self,
        endpoint_url: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        region: str = "us-east-1",
        timeout: float = 0.6,
    ) -> None:
        settings = get_settings()
        self.configured_url = (endpoint_url or os.getenv("MINIO_URL") or settings.MINIO_URL).rstrip("/")
        self.lan_url = f"http://{settings.SERVER_LAN_IP}:{settings.MINIO_PORT}"
        self.access_key = access_key or os.getenv("MINIO_ROOT_USER") or settings.MINIO_ROOT_USER
        self.secret_key = secret_key or os.getenv("MINIO_ROOT_PASSWORD") or settings.MINIO_ROOT_PASSWORD
        self.backup_bucket = os.getenv("MINIO_BACKUP_BUCKET") or getattr(settings, "MINIO_BACKUP_BUCKET", "backups")
        self.region = region
        self.timeout = timeout

    def _candidate_endpoints(self) -> List[str]:
        candidates = [
            self.configured_url,
            "http://minio:9000",
            self.lan_url,
            "http://127.0.0.1:9000",
        ]
        seen = set()
        out = []
        for u in candidates:
            clean = (u or "").rstrip("/")
            if clean and clean not in seen:
                seen.add(clean)
                out.append(clean)
        return out

    def check_health(self) -> Dict[str, Any]:
        if os.getenv("TESTING", "").lower() == "true" or "PYTEST_CURRENT_TEST" in os.environ:
            return {
                "service": "minio_s3",
                "reachable": False,
                "status": "standby_test_mode",
                "endpoint": self.configured_url,
                "backup_bucket": self.backup_bucket,
            }

        for base in self._candidate_endpoints():
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.get(f"{base}/minio/health/live")
                    if resp.status_code in (200, 204):
                        return {
                            "service": "minio_s3",
                            "reachable": True,
                            "status": "healthy",
                            "endpoint": base,
                            "backup_bucket": self.backup_bucket,
                        }
            except Exception:
                continue

        return {
            "service": "minio_s3",
            "reachable": False,
            "status": "standby",
            "endpoint": self.configured_url,
            "backup_bucket": self.backup_bucket,
        }

    def _build_sigv4_headers(
        self,
        method: str,
        endpoint: str,
        canonical_uri: str,
        query_params: Optional[Dict[str, str]],
        payload_sha256: str,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        parsed = urlparse(endpoint)
        host_header = parsed.netloc

        headers_to_sign: Dict[str, str] = {
            "host": host_header,
            "x-amz-content-sha256": payload_sha256,
            "x-amz-date": amz_date,
        }
        if extra_headers:
            for k, v in extra_headers.items():
                headers_to_sign[k.lower()] = v.strip()

        sorted_keys = sorted(headers_to_sign.keys())
        canonical_headers = "".join(f"{k}:{headers_to_sign[k]}\n" for k in sorted_keys)
        signed_headers = ";".join(sorted_keys)

        canonical_querystring = ""
        if query_params:
            canonical_querystring = urlencode(sorted(query_params.items()), quote_via=quote)

        canonical_request = "\n".join([
            method.upper(),
            canonical_uri,
            canonical_querystring,
            canonical_headers,
            signed_headers,
            payload_sha256,
        ])

        credential_scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ])

        sig_key = _signing_key(self.secret_key, date_stamp, self.region, "s3")
        signature = hmac.new(sig_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        out_headers = {
            "Host": host_header,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_sha256,
            "Authorization": (
                f"AWS4-HMAC-SHA256 Credential={self.access_key}/{credential_scope}, "
                f"SignedHeaders={signed_headers}, Signature={signature}"
            ),
        }
        if extra_headers:
            out_headers.update(extra_headers)
        return out_headers

    def ensure_bucket(self, endpoint: str, bucket: Optional[str] = None) -> bool:
        target_bucket = bucket or self.backup_bucket
        empty_hash = hashlib.sha256(b"").hexdigest()
        uri = f"/{quote(target_bucket, safe='')}"
        try:
            with httpx.Client(timeout=3.0) as client:
                head_headers = self._build_sigv4_headers("HEAD", endpoint, uri, None, empty_hash)
                head_res = client.head(f"{endpoint}{uri}", headers=head_headers)
                if head_res.status_code in (200, 204):
                    return True
                put_headers = self._build_sigv4_headers("PUT", endpoint, uri, None, empty_hash)
                put_res = client.put(f"{endpoint}{uri}", headers=put_headers, content=b"")
                return put_res.status_code in (200, 204, 409)
        except Exception:
            return False

    def upload_file(
        self,
        filepath: Path,
        object_key: str,
        bucket: Optional[str] = None,
        content_type: str = "application/octet-stream",
    ) -> Dict[str, Any]:
        """Uploads a backup file to MinIO S3 bucket using AWS SigV4."""
        target_bucket = bucket or self.backup_bucket
        health = self.check_health()
        if not health.get("reachable"):
            return {
                "synced": False,
                "bucket": target_bucket,
                "object_key": object_key,
                "reason": health.get("status", "minio_unreachable"),
            }

        endpoint = health["endpoint"]
        self.ensure_bucket(endpoint, target_bucket)

        clean_key = "/".join(quote(seg, safe="-_.~") for seg in object_key.lstrip("/").split("/"))
        uri = f"/{quote(target_bucket, safe='')}/{clean_key}"

        data = filepath.read_bytes()
        payload_hash = hashlib.sha256(data).hexdigest()
        headers = self._build_sigv4_headers(
            "PUT",
            endpoint,
            uri,
            None,
            payload_hash,
            extra_headers={"Content-Type": content_type},
        )

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.put(f"{endpoint}{uri}", headers=headers, content=data)
                if resp.status_code in (200, 201, 204):
                    return {
                        "synced": True,
                        "bucket": target_bucket,
                        "object_key": object_key,
                        "endpoint": endpoint,
                        "s3_uri": f"s3://{target_bucket}/{object_key}",
                        "size_bytes": len(data),
                    }
                return {
                    "synced": False,
                    "bucket": target_bucket,
                    "object_key": object_key,
                    "reason": f"HTTP_{resp.status_code}",
                }
        except Exception as exc:
            return {
                "synced": False,
                "bucket": target_bucket,
                "object_key": object_key,
                "reason": type(exc).__name__,
            }

    def list_objects(self, prefix: str = "pap/", bucket: Optional[str] = None) -> List[Dict[str, Any]]:
        target_bucket = bucket or self.backup_bucket
        health = self.check_health()
        if not health.get("reachable"):
            return []

        endpoint = health["endpoint"]
        uri = f"/{quote(target_bucket, safe='')}"
        query = {"list-type": "2", "prefix": prefix}
        empty_hash = hashlib.sha256(b"").hexdigest()
        headers = self._build_sigv4_headers("GET", endpoint, uri, query, empty_hash)

        try:
            with httpx.Client(timeout=4.0) as client:
                resp = client.get(f"{endpoint}{uri}", params=query, headers=headers)
                if resp.status_code != 200 or not resp.text:
                    return []
                root = ET.fromstring(resp.text)
                items: List[Dict[str, Any]] = []
                for elem in root.iter():
                    if elem.tag.endswith("Contents"):
                        key_val = ""
                        size_val = 0
                        last_mod = ""
                        for child in elem:
                            if child.tag.endswith("Key") and child.text:
                                key_val = child.text
                            elif child.tag.endswith("Size") and child.text:
                                size_val = int(child.text)
                            elif child.tag.endswith("LastModified") and child.text:
                                last_mod = child.text
                        if key_val:
                            items.append({
                                "key": key_val,
                                "size_bytes": size_val,
                                "last_modified": last_mod,
                                "bucket": target_bucket,
                            })
                return items
        except Exception:
            return []
