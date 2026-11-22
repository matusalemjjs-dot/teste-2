"""Acesso ao object storage privado compatível com S3.

- O bucket é privado; o acesso a arquivos acontece apenas por URLs assinadas e temporárias.
- As chaves dos objetos são geradas pelo servidor (nunca a partir do nome enviado pelo usuário).
- Dois clientes: um para o endpoint interno (backend/worker) e outro para o endpoint público, usado
  para assinar URLs entregues ao navegador.
"""

import uuid
from dataclasses import dataclass
from functools import lru_cache

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import Settings, get_settings


class StorageError(Exception):
    pass


@dataclass(frozen=True)
class ObjectInfo:
    size_bytes: int
    content_type: str | None
    etag: str | None


def source_video_key(owner_id: uuid.UUID, project_id: uuid.UUID, asset_id: uuid.UUID) -> str:
    return f"users/{owner_id}/projects/{project_id}/source/{asset_id}.mp4"


def clip_key(owner_id: uuid.UUID, project_id: uuid.UUID, clip_id: uuid.UUID, version: int, ext: str) -> str:
    return f"users/{owner_id}/projects/{project_id}/clips/{clip_id}/v{version}.{ext}"


def project_prefix(owner_id: uuid.UUID, project_id: uuid.UUID) -> str:
    return f"users/{owner_id}/projects/{project_id}/"


class Storage:
    def __init__(self, settings: Settings):
        self.bucket = settings.s3_bucket
        self._internal = self._client(settings, settings.s3_endpoint_url)
        self._public = self._client(settings, settings.s3_public_endpoint_url or settings.s3_endpoint_url)

    @staticmethod
    def _client(settings: Settings, endpoint: str | None) -> BaseClient:
        kwargs = {}
        if settings.s3_access_key_id and settings.s3_secret_access_key:
            kwargs = {
                "aws_access_key_id": settings.s3_access_key_id.get_secret_value(),
                "aws_secret_access_key": settings.s3_secret_access_key.get_secret_value(),
            }
        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=settings.s3_region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=30,
            ),
            **kwargs,
        )

    def presign_upload(self, key: str, content_type: str, max_bytes: int, expires: int) -> dict:
        """POST pré-assinado: o próprio storage recusa arquivos acima de `max_bytes` ou com outro tipo
        (em provedores que aplicam a política, como AWS S3 e MinIO). O tamanho é conferido de novo no
        finalize-upload."""
        try:
            return self._public.generate_presigned_post(
                Bucket=self.bucket,
                Key=key,
                Fields={"Content-Type": content_type},
                Conditions=[["content-length-range", 1, max_bytes], {"Content-Type": content_type}],
                ExpiresIn=expires,
            )
        except ClientError as exc:
            raise StorageError("Falha ao gerar URL de upload") from exc

    def presign_get(self, key: str, expires: int, *, public: bool, download_name: str | None = None) -> str:
        params = {"Bucket": self.bucket, "Key": key}
        if download_name:
            params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
        client = self._public if public else self._internal
        return client.generate_presigned_url("get_object", Params=params, ExpiresIn=expires)

    def head(self, key: str) -> ObjectInfo | None:
        try:
            r = self._internal.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise StorageError("Falha ao consultar o armazenamento") from exc
        return ObjectInfo(size_bytes=r["ContentLength"], content_type=r.get("ContentType"), etag=r.get("ETag"))

    def read_range(self, key: str, start: int, end: int) -> bytes:
        try:
            r = self._internal.get_object(Bucket=self.bucket, Key=key, Range=f"bytes={start}-{end}")
            return r["Body"].read()
        except ClientError as exc:
            raise StorageError("Falha ao ler o arquivo") from exc

    def upload_file(self, path, key: str, content_type: str) -> None:
        try:
            self._internal.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": content_type})
        except (ClientError, OSError) as exc:
            raise StorageError("Falha ao enviar arquivo ao armazenamento") from exc

    def delete(self, key: str) -> None:
        try:
            self._internal.delete_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            raise StorageError("Falha ao excluir arquivo") from exc

    def delete_prefix(self, prefix: str) -> int:
        """Exclui todos os objetos sob o prefixo. Retorna a quantidade excluída."""
        count = 0
        try:
            paginator = self._internal.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if objs:
                    self._internal.delete_objects(Bucket=self.bucket, Delete={"Objects": objs, "Quiet": True})
                    count += len(objs)
        except ClientError as exc:
            raise StorageError("Falha ao excluir arquivos") from exc
        return count

    def list_keys(self, prefix: str) -> list[str]:
        r = self._internal.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        return [o["Key"] for o in r.get("Contents", [])]


@lru_cache
def get_storage() -> Storage:
    return Storage(get_settings())


def iter_object(storage: Storage, key: str, chunk_size: int = 1024 * 1024):
    """Lê um objeto em blocos (streaming), sem carregar o arquivo inteiro na memória."""
    try:
        body = storage._internal.get_object(Bucket=storage.bucket, Key=key)["Body"]
        yield from body.iter_chunks(chunk_size)
    except ClientError as exc:
        raise StorageError("Falha ao baixar o arquivo") from exc
