# Ke hoach kiem thu logs voi ELK cho AI Algorithm Service

## 1. Muc tieu

Kiem tra end-to-end luong log hien tai:

```text
ai-runtime / ai-ops stdout
  -> Docker json-file logs
  -> Logstash file input
  -> Elasticsearch index alias ai-service-logs
  -> Kibana Discover / filter / dashboard
```

Ket qua mong muon:

- Log tu `ai-runtime` va `ai-ops` duoc day vao Elasticsearch.
- Co the tim log theo `request_id`, service, path, status, latency.
- Co the phan biet log request thanh cong, request loi validation, loi readiness, loi sync/ops.
- Co checklist ro rang de ket luan ELK dang hoat dong dung.

## 2. Hien trang cau hinh trong repo

Docker compose da co cac service ELK:

- `elasticsearch`: port `9200`.
- `logstash`: doc file `/var/lib/docker/containers/*/*.log`.
- `kibana`: port `5601`.

Container `ai-runtime` va `ai-ops` da cau hinh:

- Docker logging driver: `json-file`.
- Label `ai_log=true`.
- Label `com.docker.compose.service`.

Logstash pipeline hien tai:

- Chi giu log co `attrs.ai_log == "true"`.
- Chi giu service `ai-runtime` va `ai-ops`.
- Parse Docker field `log` thanh `message`.
- Neu `message` la JSON log cua app thi parse vao field `app`.
- Drop log `DEBUG`.
- Ghi vao Elasticsearch voi ILM alias `ai-service-logs`.

Luu y: pipeline dang doc Docker log tu `start_position => "end"`, nen sau khi Logstash start, nen phat sinh log moi de test.

## 3. Chuan bi moi truong

### 3.1. Xac nhan service chinh dang chay

Chay trong thu muc `ai-algorithm-service`:

```bash
docker compose --profile app ps
docker compose logs --tail=50 ai-runtime
docker compose logs --tail=50 ai-ops
```

Ky vong:

- `ai-runtime` running va expose `8001:8000`.
- `ai-ops` running va expose `8002:8002`.
- Log stdout cua hai service co du lieu.

### 3.2. Khoi dong ELK stack

```bash
cd ai-algorithm-service
docker compose --profile observability up -d elasticsearch logstash kibana
```

Kiem tra trang thai:

```bash
docker compose ps elasticsearch logstash kibana
curl http://localhost:9200
curl http://localhost:5601/api/status
```

Ky vong:

- Elasticsearch tra JSON co `cluster_name`.
- Kibana status eventually `available`.
- Logstash khong restart lien tuc.

Neu Logstash khong co quyen doc `/var/lib/docker/containers`, kiem tra service `logstash` dang chay voi `user: "0:0"` va volume mount read-only da ton tai.

## 4. Tao log mau de kiem thu

Dung request id co tien to rieng de de tim trong Kibana:

```bash
export RID_PREFIX="elk-test-$(date +%Y%m%d-%H%M%S)"
```

### 4.1. Health request thanh cong

```bash
curl -i http://localhost:8001/health \
  -H "X-Request-Id: ${RID_PREFIX}-health"
```

Ky vong:

- HTTP `200`.
- Response header co `X-Request-Id`.
- Log co `request_id=<...>-health method=GET path=/health status=200`.

### 4.2. Readiness request

```bash
curl -i http://localhost:8001/ready \
  -H "X-Request-Id: ${RID_PREFIX}-ready"
```

Ky vong:

- HTTP `200` neu runtime ready, hoac `503` neu chua ready.
- Ca hai truong hop deu phai co log request trong ELK.

### 4.3. Inference validation error co chu dich

```bash
curl -i -X POST http://localhost:8001/api/algorithm/ai \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: ${RID_PREFIX}-invalid-inference" \
  -d '{"areaId":1,"crosses":[]}'
```

Ky vong:

- HTTP loi ung dung, vi `crosses` rong.
- Log telemetry co `path=/api/algorithm/ai`.
- Log app co the co error/validation message.
- Muc dich testcase nay la kiem tra log loi co vao ELK, khong phai test inference thanh cong.

### 4.4. Ops endpoint health

```bash
curl -i http://localhost:8002/health \
  -H "X-Request-Id: ${RID_PREFIX}-ops-health"
```

Ky vong:

- HTTP `200`.
- Log xuat hien voi service `ai-ops`.

### 4.5. Ops auth error co chu dich

Goi sync endpoint khong co `X-Internal-API-Key` de tao log loi auth/validation:

```bash
curl -i -X PUT http://localhost:8002/internal/sync/areas/1/real-network \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: ${RID_PREFIX}-ops-auth-error" \
  -d '{"sourceEventId":"elk-auth-error","area":{},"crosses":[]}'
```

Ky vong:

- HTTP `401`, `403`, hoac loi validation tuy handler hien tai.
- Log van vao ELK voi request id tuong ung.

## 5. Kiem tra Elasticsearch bang API

Cho Logstash ingest trong 5-15 giay, sau do kiem tra:

```bash
curl "http://localhost:9200/_cat/indices?v"
curl "http://localhost:9200/_cat/aliases?v"
```

Ky vong:

- Co index hoac alias lien quan `ai-service-logs`.
- Neu ILM tao index, co the thay index dang sau alias, vi du `ai-service-logs-000001`.

Search theo request id:

```bash
curl -s "http://localhost:9200/ai-service-logs*/_search?pretty" \
  -H "Content-Type: application/json" \
  -d "{
    \"size\": 10,
    \"query\": {
      \"query_string\": {
        \"query\": \"${RID_PREFIX}*\"
      }
    },
    \"sort\": [{\"@timestamp\": \"desc\"}]
  }"
```

Ky vong:

- Co hits tu `ai-runtime` va `ai-ops`.
- Field nen quan sat:
  - `service`
  - `message`
  - `app.level`
  - `app.logger`
  - `app.message`
  - `@timestamp`
  - `container_id`

## 6. Kiem tra tren Kibana

Mo:

```text
http://localhost:5601
```

### 6.1. Tao Data View

Vao `Stack Management -> Data Views -> Create data view`.

Gia tri de xuat:

- Name: `AI Service Logs`
- Index pattern: `ai-service-logs*`
- Timestamp field: `@timestamp`

### 6.2. Discover queries can kiem tra

Trong Discover, thu cac query:

```text
app.message : "*elk-test*"
```

```text
service : "ai-runtime"
```

```text
service : "ai-ops"
```

```text
app.level : "ERROR" or app.level : "WARNING"
```

```text
app.message : "*path=/api/algorithm/ai*"
```

```text
app.message : "*status=200*"
```

Ky vong:

- Tim duoc request theo `RID_PREFIX`.
- Thay duoc latency trong `app.message`, vi telemetry hien dang log dang chuoi:
  `request_id=... method=... path=... status=... latency_ms=...`.

## 7. Testcase pass/fail

| Ma | Muc tieu | Cach test | Pass khi |
|---|---|---|---|
| ELK-01 | Elasticsearch reachable | `curl localhost:9200` | Tra JSON cluster |
| ELK-02 | Kibana reachable | Mo `localhost:5601` | UI vao duoc |
| ELK-03 | Logstash running | `docker compose ps logstash` | Container healthy/running |
| ELK-04 | Runtime health log ingest | Goi `/health` voi request id | Tim thay trong `ai-service-logs*` |
| ELK-05 | Runtime error log ingest | Goi inference invalid | Tim thay status loi/path inference |
| ELK-06 | Ops log ingest | Goi `8002/health` | Tim thay `service=ai-ops` |
| ELK-07 | Loc dung service | Xem index sau khi app chay | Khong co log mysql/minio/kibana trong index |
| ELK-08 | Timestamp dung | So sanh gio log voi thoi gian request | `@timestamp` gan dung thoi diem test |
| ELK-09 | Request id trace duoc | Query theo `${RID_PREFIX}` | Tra du log tu cac request test |
| ELK-10 | Loi duoc quan sat | Query `app.level: ERROR OR WARNING` | Thay loi co chu dich hoac warning lien quan |

## 8. Kich ban kiem thu nang cao

### 8.1. Kiem thu inference thanh cong

Neu runtime da co area/model/bundle ready, gui payload inference hop le tu:

```text
docs/api-payload-examples/inference-compact-request.json
```

Lenh:

```bash
curl -i -X POST http://localhost:8001/api/algorithm/ai \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: ${RID_PREFIX}-valid-inference" \
  -d @docs/api-payload-examples/inference-compact-request.json
```

Ky vong:

- HTTP `200`.
- Log co `status=200`.
- Audit/inference log co the hien `Area ... inference xong`.

### 8.2. Kiem thu latency

Chay nhieu request lien tiep:

```bash
for i in $(seq 1 20); do
  curl -s http://localhost:8001/health \
    -H "X-Request-Id: ${RID_PREFIX}-load-$i" >/dev/null
done
```

Trong Kibana, query:

```text
app.message : "*${RID_PREFIX}-load*"
```

Ky vong:

- Du 20 log request.
- Co the doc `latency_ms` trong message.

### 8.3. Kiem thu restart Logstash

```bash
docker compose restart logstash
curl -i http://localhost:8001/health \
  -H "X-Request-Id: ${RID_PREFIX}-after-logstash-restart"
```

Ky vong:

- Log moi sau restart van vao Elasticsearch.
- Log cu co the khong doc lai neu `sincedb` da ghi offset; day la hanh vi binh thuong.

## 9. Rủi ro va diem can chu y

- `start_position => "end"`: Logstash chi doc log moi sau khi no start. Neu Kibana trong, hay phat sinh request moi.
- Docker labels trong file log phu thuoc logging driver `json-file`. Neu doi driver sang Fluentd/Loki/syslog, pipeline nay can doi.
- Log telemetry hien parse `request_id`, `path`, `status`, `latency_ms` nam trong chuoi `app.message`, chua tach thanh field rieng. Neu can dashboard tot hon, nen bo sung filter grok trong Logstash.
- `LOG_JSON=true` la mac dinh; neu tat JSON log thi Logstash van ingest nhung field `app.*` se thieu.
- Elasticsearch/Kibana local co the can RAM kha lon; neu may yeu, tang Docker memory hoac chay tung service.

## 10. De xuat cai tien sau khi kiem thu

Sau khi xac nhan pipeline co log, nen can nhac:

1. Tach field telemetry trong Logstash:
   - `request_id`
   - `http.method`
   - `url.path`
   - `http.status_code`
   - `event.duration_ms`
2. Tao Kibana saved searches:
   - Runtime errors.
   - Inference latency.
   - Request trace by request id.
   - Ops sync/bundle lifecycle.
3. Them dashboard co cac panel:
   - Count request theo status.
   - Top error messages.
   - Latency p50/p95 neu da tach field numeric.
   - Logs grouped theo `service`.
4. Them runbook ngan:
   - Khi inference loi thi query theo `X-Request-Id`.
   - Khi bundle sync loi thi query `service=ai-ops`.
   - Khi runtime not ready thi query `/ready` va `AREA_NOT_READY`.

## 11. Tieu chi hoan thanh

Kiem thu ELK duoc xem la dat khi:

- Hoan thanh cac testcase ELK-01 den ELK-09.
- Tim duoc it nhat mot log `ai-runtime` va mot log `ai-ops` trong Kibana.
- Tim duoc mot request cu the bang `X-Request-Id`.
- Tim duoc mot request loi co chu dich.
- Ghi lai bat ky sai lech nao giua log mong doi va log thuc te de quyet dinh co can sua pipeline Logstash hay logger app khong.
