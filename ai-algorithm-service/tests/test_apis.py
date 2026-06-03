def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    data = resp.json()
    assert data["status"] == "ok"


def test_list_areas(client):
    resp = client.get("/api/algorithm/ai/areas")
    assert resp.status_code == 200
    data = resp.json()
    assert "areas" in data
    assert isinstance(data["areas"], list)


def test_ai_algorithm_no_model(client):
    """Test that the API returns an error when no model is available."""
    payload = {
        "crosses": [
            {
                "id": 1,
                "areaId": 99999,
                "cycle": {
                    "id": 1,
                    "createdDate": "2026-01-01",
                    "cycleLength": 90
                },
                "stages": [
                    {
                        "id": 1,
                        "stageCode": "P1",
                        "oldId": "S1",
                        "yellow": 3,
                        "redClear": 2,
                        "duration": 45
                    },
                    {
                        "id": 2,
                        "stageCode": "P2",
                        "oldId": "S2",
                        "yellow": 3,
                        "redClear": 2,
                        "duration": 45
                    }
                ],
                "roads": [
                    {
                        "id": 1,
                        "direction": 1,
                        "saturationFlow": 1800,
                        "averageSpeed": 30,
                        "occupancySpace": 45.0
                    },
                    {
                        "id": 2,
                        "direction": 3,
                        "saturationFlow": 1800,
                        "averageSpeed": 28,
                        "occupancySpace": 40.0
                    }
                ]
            }
        ]
    }

    resp = client.post("/api/algorithm/ai", json=payload)
    # Area 99999 không tồn tại → API trả 400 / 404 / 409 (AREA_NOT_READY)
    assert resp.status_code in (400, 404, 409)
    data = resp.json()
    assert "errorCode" in data


def test_ai_algorithm_empty_crosses(client):
    """Test that the API validates empty crosses list."""
    payload = {
        "crosses": [],
        "modelId": "default"
    }

    resp = client.post("/api/algorithm/ai", json=payload)
    assert resp.status_code == 400


def test_ai_algorithm_validation_error(client):
    """Test that invalid input returns 422."""
    payload = {
        "crosses": "not_a_list"
    }

    resp = client.post("/api/algorithm/ai", json=payload)
    assert resp.status_code == 422
