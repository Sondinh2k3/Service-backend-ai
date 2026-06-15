#!/bin/sh
set -eu

ELASTICSEARCH_URL="${ELASTICSEARCH_URL:-http://elasticsearch:9200}"
POLICY_NAME="${POLICY_NAME:-ai-service-logs-7d}"
ALIAS_NAME="${ALIAS_NAME:-ai-service-logs}"
INDEX_NAME="${INDEX_NAME:-ai-service-logs-000001}"
POLICY_FILE="${POLICY_FILE:-/ilm-policy.json}"
MAPPING_FILE="/tmp/ai-service-log-mapping.json"
UPDATE_MAPPING_FILE="/tmp/ai-service-log-update-mapping.json"
INDEX_FILE="/tmp/ai-service-log-index.json"
ALIAS_FILE="/tmp/ai-service-log-alias.json"

cat >"${MAPPING_FILE}" <<'JSON'
{
  "properties": {
    "@timestamp": {"type": "date"},
    "request_id": {"type": "keyword"},
    "container_id": {"type": "keyword"},
    "area_id": {"type": "integer"},
    "network_id": {"type": "keyword"},
    "bundle_id": {"type": "keyword"},
    "error_code": {"type": "keyword"},
    "service": {
      "properties": {
        "name": {"type": "keyword"},
        "role": {"type": "keyword"}
      }
    },
    "log": {
      "properties": {
        "level": {"type": "keyword"}
      }
    },
    "trace": {
      "properties": {
        "step": {"type": "keyword"}
      }
    },
    "http": {
      "properties": {
        "request": {
          "properties": {
            "method": {"type": "keyword"}
          }
        },
        "response": {
          "properties": {
            "status_code": {"type": "integer"}
          }
        }
      }
    },
    "url": {
      "properties": {
        "path": {"type": "keyword"}
      }
    },
    "event": {
      "properties": {
        "action": {"type": "keyword"},
        "status": {"type": "keyword"},
        "duration_ms": {"type": "integer"},
        "step_duration_ms": {"type": "integer"}
      }
    },
    "app": {
      "properties": {
        "time": {"type": "date"},
        "level": {"type": "keyword"},
        "logger": {"type": "keyword"},
        "service_name": {"type": "keyword"},
        "service_role": {"type": "keyword"},
        "request_id": {"type": "keyword"},
        "event": {"type": "keyword"},
        "event_status": {"type": "keyword"},
        "trace_step": {"type": "keyword"},
        "http_method": {"type": "keyword"},
        "http_path": {"type": "keyword"},
        "http_status": {"type": "integer"},
        "latency_ms": {"type": "integer"},
        "duration_ms": {"type": "integer"},
        "area_id": {"type": "integer"},
        "network_id": {"type": "keyword"},
        "bundle_id": {"type": "keyword"},
        "tenant_id": {"type": "keyword"},
        "source_event_id": {"type": "keyword"},
        "checksum": {"type": "keyword"},
        "error_code": {"type": "keyword"},
        "error_type": {"type": "keyword"}
      }
    }
  }
}
JSON

cat >"${UPDATE_MAPPING_FILE}" <<'JSON'
{
  "properties": {
    "area_id": {"type": "integer"},
    "network_id": {"type": "keyword"},
    "bundle_id": {"type": "keyword"},
    "error_code": {"type": "keyword"},
    "event": {
      "properties": {
        "action": {"type": "keyword"},
        "status": {"type": "keyword"},
        "step_duration_ms": {"type": "integer"}
      }
    },
    "log": {
      "properties": {
        "level": {"type": "keyword"}
      }
    },
    "service": {
      "properties": {
        "role": {"type": "keyword"}
      }
    },
    "trace": {
      "properties": {
        "step": {"type": "keyword"}
      }
    },
    "app": {
      "properties": {
        "event": {"type": "keyword"},
        "event_status": {"type": "keyword"},
        "trace_step": {"type": "keyword"},
        "duration_ms": {"type": "integer"},
        "area_id": {"type": "integer"},
        "network_id": {"type": "keyword"},
        "bundle_id": {"type": "keyword"},
        "tenant_id": {"type": "keyword"},
        "source_event_id": {"type": "keyword"},
        "checksum": {"type": "keyword"},
        "error_code": {"type": "keyword"},
        "error_type": {"type": "keyword"}
      }
    }
  }
}
JSON

cat >"${INDEX_FILE}" <<JSON
{
  "settings": {
    "index.lifecycle.name": "${POLICY_NAME}",
    "index.lifecycle.rollover_alias": "${ALIAS_NAME}"
  },
  "mappings": $(cat "${MAPPING_FILE}"),
  "aliases": {
    "${ALIAS_NAME}": {
      "is_write_index": true
    }
  }
}
JSON

cat >"${ALIAS_FILE}" <<JSON
{
  "actions": [
    {
      "add": {
        "index": "${INDEX_NAME}",
        "alias": "${ALIAS_NAME}",
        "is_write_index": true
      }
    }
  ]
}
JSON

echo "Waiting for Elasticsearch at ${ELASTICSEARCH_URL}..."
until curl -fsS -o /dev/null "${ELASTICSEARCH_URL}"; do
  sleep 2
done

echo "Installing ILM policy ${POLICY_NAME}..."
curl -fsS -X PUT "${ELASTICSEARCH_URL}/_ilm/policy/${POLICY_NAME}" \
  -H "Content-Type: application/json" \
  --data-binary "@${POLICY_FILE}"
echo

if curl -fsS -o /dev/null "${ELASTICSEARCH_URL}/_alias/${ALIAS_NAME}"; then
  echo "Alias ${ALIAS_NAME} already exists; keeping current write index."
elif curl -fsS -o /dev/null "${ELASTICSEARCH_URL}/${INDEX_NAME}"; then
  echo "Index ${INDEX_NAME} already exists; adding alias ${ALIAS_NAME}."
  curl -fsS -X POST "${ELASTICSEARCH_URL}/_aliases" \
    -H "Content-Type: application/json" \
    --data-binary "@${ALIAS_FILE}"
  echo
else
  echo "Creating write index ${INDEX_NAME} with alias ${ALIAS_NAME}..."
  curl -fsS -X PUT "${ELASTICSEARCH_URL}/${INDEX_NAME}" \
    -H "Content-Type: application/json" \
    --data-binary "@${INDEX_FILE}"
  echo
fi

echo "Ensuring index mappings for Kibana data view..."
curl -fsS -X PUT "${ELASTICSEARCH_URL}/${ALIAS_NAME}*/_mapping" \
  -H "Content-Type: application/json" \
  --data-binary "@${UPDATE_MAPPING_FILE}"
echo

echo "Elasticsearch log index initialization completed."
