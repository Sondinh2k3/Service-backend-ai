# AI Algorithm Service - Integration Handover

Tai lieu nay dung cho ben tich hop phan mem khi trien khai AI Algorithm
Service tu image tar va docker compose production.

## 1. Files ban giao

Ben AI ban giao cac file sau:

```text
ai-algorithm-service_1.0.0.tar
docker-compose.production.yml
.env.production.example
ai_service_internal_db_1.0.0.sql
```

Trong do:

- `ai-algorithm-service_1.0.0.tar`: Docker image cho ca `ai-runtime` va `ai-ops`.
- `docker-compose.production.yml`: compose production, khong chay kem MySQL/MinIO local.
- `.env.production.example`: file mau bien moi truong, ben tich hop copy thanh `.env.production`.
- `ai_service_internal_db_1.0.0.sql`: DB dump/schema noi bo cua AI service.

## 2. Docker image

Load image:

```bash
docker load -i ai-algorithm-service_1.0.0.tar
docker images | grep ai-algorithm-service
```

Image tag can co:

```text
ai-algorithm-service:1.0.0
```

`ai-runtime` va `ai-ops` dung chung image nay, khac nhau qua bien
`SERVICE_ROLE`.

## 3. Database

AI service can mot database noi bo rieng de luu:

- area registry
- real network snapshot
- model bundle metadata
- active/deployment events
- inference audit
- drift event

Ben tich hop tao database va import dump:

```bash
mysql -u <db_admin_user> -p -e "CREATE DATABASE IF NOT EXISTS <db_name> CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -u <db_admin_user> -p <db_name> < ai_service_internal_db_1.0.0.sql
```

Sau do cap user rieng cho AI service.

Gia tri can dien vao `.env.production`:

```env
DATABASE_URL=mysql+pymysql://<ai_db_user>:<ai_db_password>@<db_host>:<db_port>/<db_name>
```

Quyen DB toi thieu:

```text
SELECT, INSERT, UPDATE, DELETE
```

Neu khong import schema day du truoc khi chay service, user AI service can them:

```text
CREATE, ALTER, INDEX
```

Khuyen nghi production: import schema/dump truoc, sau do chi cap quyen CRUD neu
chinh sach bao mat yeu cau han che DDL.

## 4. MinIO / S3 artifact storage

Ben tich hop da co MinIO va se tao bucket cho AI service.

Thong tin can cung cap:

```text
MINIO endpoint:
Bucket:
Access key:
Secret key:
Secure HTTPS: true/false
Region: optional
```

Gia tri dien vao `.env.production`:

```env
MINIO_ENABLED=true
MINIO_ENDPOINT=http://<minio-host>:9000
MINIO_ACCESS_KEY=<access-key>
MINIO_SECRET_KEY=<secret-key>
MINIO_BUCKET=<bucket-name>
MINIO_SECURE=false
MINIO_REGION=
MINIO_PREFIX=models
ARTIFACT_BUNDLE_PREFIX=bundles
```

Quyen MinIO can co:

- `list/read` bucket de auto-sync sim/runtime bundle.
- `write` neu bat upload runtime bundle sau khi compose.

Voi cau hinh hien tai:

```env
SIM_BUNDLE_UPLOAD_RUNTIME=true
MINIO_UPLOAD_ON_SYNC=true
```

thi access key can co quyen ghi vao bucket/prefix tuong ung.

Prefix mac dinh cho sim bundle:

```env
MINIO_AUTO_SYNC_PREFIX=sim/default/
SIM_BUNDLE_PREFIX=sim/default/
SIM_BUNDLE_SUFFIX=.sim.zip
```

Nghia la file sim bundle nen nam duoi:

```text
s3://<bucket>/sim/default/<file>.sim.zip
```

Neu ben tich hop dung tenant/network rieng, co the doi prefix thanh:

```env
MINIO_AUTO_SYNC_PREFIX=sim/<tenant_id>/<network_id>/
SIM_BUNDLE_PREFIX=sim/<tenant_id>/<network_id>/
```

## 5. Internal API key

AI service bao ve cac API sync/ops/internal bang header:

```http
X-Internal-API-Key: <INTERNAL_API_KEY>
```

Ben tich hop can tao mot key manh va dien vao `.env.production`:

```env
INTERNAL_API_KEY=<strong-shared-key>
INTERNAL_API_KEY_HEADER=X-Internal-API-Key
```

Ben Core Controller / backend khi goi cac endpoint internal phai gui header nay.

Khong dung key demo/local trong production.

## 6. Production flags

Khuyen nghi giu cac cau hinh sau:

```env
APP_ENV=production
AI_STRICT_MODE=true
BUNDLE_LAYOUT_ENABLED=true
STARTUP_PREFLIGHT=true
ENFORCE_SINGLE_AREA_PER_REQUEST=true
```

Khuyen nghi go-live an toan:

```env
MINIO_AUTO_SYNC_AUTO_ACTIVATE=false
SIM_BUNDLE_AUTO_ACTIVATE=false
```

Ly do: can review `compatibility_report.json` truoc khi active model.

## 7. Run service

Tai thu muc chua `docker-compose.production.yml`:

```bash
cp .env.production.example .env.production
# Sua .env.production voi DB/MinIO/API key that.
docker compose -f docker-compose.production.yml up -d
```

Kiem tra:

```bash
docker compose -f docker-compose.production.yml ps
curl http://localhost:8001/health
curl http://localhost:8001/ready
curl -H "X-Internal-API-Key: <INTERNAL_API_KEY>" \
  http://localhost:8002/ops/auto-sync/status
```

## 8. Network ports

Mac dinh compose expose:

```text
8001 -> ai-runtime public inference/readiness
8002 -> ai-ops sync/model lifecycle/admin
```

Core Controller goi inference qua `ai-runtime`.
Backend/operator goi sync/ops qua `ai-ops`.

## 9. Checklist truoc go-live

- Da load image `ai-algorithm-service:1.0.0`.
- Da import DB dump/schema.
- `.env.production` da dien DB URL dung.
- `.env.production` da dien MinIO endpoint/bucket/access key/secret.
- Bucket/prefix MinIO da ton tai va service account co quyen read/write phu hop.
- `INTERNAL_API_KEY` da duoc thong nhat voi Core/backend.
- `APP_ENV=production` va `AI_STRICT_MODE=true`.
- `SIM_BUNDLE_AUTO_ACTIVATE=false` neu can review manual.
- `/health` tra OK.
- `/ready` chi OK sau khi area/model bundle san sang.
