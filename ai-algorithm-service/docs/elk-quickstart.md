# ELK Quickstart

Use this only for local log exploration. Production observability should use the customer/vendor monitoring stack.

## Start stack

```bash
cd ai-algorithm-service
docker compose up -d elasticsearch kibana logstash
```

## Verify Elasticsearch

```bash
curl http://localhost:9200
```

## Open Kibana

```text
http://localhost:5601
```

Create an index pattern for the log index used by the compose stack, then filter by:

- `service.name`
- `request_id`
- `bundle_id`
- `network_id`
- `area_id`

## Generate sample logs

```bash
curl http://localhost:8001/health
curl http://localhost:8001/ready
```

For inference logs, include `X-Request-Id`:

```bash
curl -X POST http://localhost:8001/api/algorithm/ai \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: elk-demo-001" \
  -d '{"crosses":[]}'
```

The request may fail validation; that is fine for checking log ingestion.

## Troubleshooting

| Symptom | Check |
|---|---|
| Kibana has no data | Logstash container running, index pattern exists |
| Elasticsearch not reachable | `docker compose ps elasticsearch` |
| Missing request id | Caller did not set `X-Request-Id` |

More: [troubleshooting.md](troubleshooting.md).
