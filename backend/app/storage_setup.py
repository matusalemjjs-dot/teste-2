"""Prepara o bucket: cria (se não existir), bloqueia acesso público e aplica CORS para o upload direto do navegador.

Uso: python -m app.storage_setup
"""

from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.services.storage import get_storage


def setup() -> list[str]:
    s = get_settings()
    client = get_storage()._internal
    done: list[str] = []
    try:
        client.head_bucket(Bucket=s.s3_bucket)
    except ClientError:
        client.create_bucket(Bucket=s.s3_bucket)
        done.append("bucket criado")
    try:
        keys = ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")
        block = dict.fromkeys(keys, True)
        client.put_public_access_block(Bucket=s.s3_bucket, PublicAccessBlockConfiguration=block)
        done.append("acesso público bloqueado")
    except ClientError as exc:  # alguns provedores (ex.: MinIO) não implementam
        done.append(f"bloqueio público não suportado pelo provedor ({exc.response['Error'].get('Code')})")
    try:
        client.put_bucket_cors(Bucket=s.s3_bucket, CORSConfiguration={"CORSRules": [{
            "AllowedOrigins": s.s3_cors_origins, "AllowedMethods": ["POST", "GET", "HEAD"],
            "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3600}]})
        done.append(f"CORS aplicado para {', '.join(s.s3_cors_origins)}")
    except ClientError as exc:
        done.append(f"CORS não suportado pelo provedor ({exc.response['Error'].get('Code')}); configure no provedor")
    return done


if __name__ == "__main__":
    for line in setup():
        print(line)
