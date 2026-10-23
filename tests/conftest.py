"""测试环境准备：使用独立的测试数据库，避免影响开发数据。"""
import os
import uuid

import pytest

os.environ["MONGO_URI"] = os.environ.get("TEST_MONGO_URI", "mongodb://localhost:27017")
os.environ["DB_NAME"] = "survey_test_db"
os.environ["SECRET"] = "test-secret"

COLLECTIONS = ("users", "surveys", "questions", "responses", "question_bank")


@pytest.fixture(scope="session")
def mongo_ok():
    from app.db import get_client
    try:
        get_client().admin.command("ping")
        return True
    except Exception:
        return False


@pytest.fixture()
def app(mongo_ok):
    if not mongo_ok:
        pytest.skip("MongoDB 不可用，跳过集成测试")
    from app import create_app
    from app.db import get_db
    for name in COLLECTIONS:
        get_db()[name].drop()
    application = create_app()
    yield application
    for name in COLLECTIONS:
        get_db()[name].drop()


@pytest.fixture()
def client(app):
    return app.test_client()


def _register(client):
    username = "u" + uuid.uuid4().hex[:10]
    r = client.post("/api/register", json={"username": username, "password": "pass123"})
    assert r.status_code == 201, r.get_json()
    data = r.get_json()
    return {"username": username, "token": data["token"], "id": data["user"]["id"]}


@pytest.fixture()
def user(client):
    return _register(client)


@pytest.fixture()
def other_user(client):
    return _register(client)


def auth(u):
    return {"Authorization": "Bearer " + u["token"]}
