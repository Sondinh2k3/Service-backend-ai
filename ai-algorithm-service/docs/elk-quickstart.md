# ELK Quickstart (Local)

Muc tieu: xem log cua ai-runtime/ai-ops tren Kibana khi chay local Docker Compose.

## 1. Start ELK stack

```bash
cd ai-algorithm-service
docker compose --profile observability up -d
```

Neu Elasticsearch khong len do `vm.max_map_count`:

```bash
sudo sysctl -w vm.max_map_count=262144
```

## 2. Verify Elasticsearch

```bash
curl http://localhost:9200/_cluster/health
```

Expected: `status` = `yellow` hoac `green`.

## 3. Tao Index Pattern trong Kibana

Mo Kibana: http://localhost:5601

- Vao Stack Management -> Data Views
- Tao data view: `ai-service-logs-*`
- Chon time field: `@timestamp`

## 4. Tao log mau de test

```bash
curl http://localhost:8001/health
curl http://localhost:8002/health
```

Vao Kibana -> Discover -> chon `ai-service-logs-*`.

## 5. Filter theo service

Neu log co field `service`, co the filter:

```
service : "ai-runtime"
```

Hoac filter theo `container_id` neu `service` chua co.

## 6. Check Logstash pipeline

Logstash doc log tu `/var/lib/docker/containers/*/*.log` theo config:

- `observability/logstash/pipeline.conf`

Kiem tra Logstash:

```bash
docker compose logs logstash | tail -n 50
```

## 7. Troubleshooting nhanh

- Elasticsearch khong len: set `vm.max_map_count` nhu buoc 1.
- Khong co index: check `http://localhost:9200/_cat/indices?v`.
- Kibana khong thay data: tao lai data view `ai-service-logs-*`.
