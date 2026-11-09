from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_read_root():
    r = client.get("/")
    assert r.status_code == 200
    assert r.json() == {"Hello": "World"}


def test_read_item():
    r = client.get("/items/1", params={"q": "abc"})
    assert r.status_code == 200
    assert r.json() == {"item_id": 1, "q": "abc"}
