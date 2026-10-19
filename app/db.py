"""MongoDB 连接与索引管理（对应设计文档第四节）。"""
from pymongo import ASCENDING, MongoClient

from app import config

_client = None


def get_client():
    global _client
    if _client is None:
        _client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=3000)
    return _client


def get_db():
    return get_client()[config.DB_NAME]


def ensure_indexes():
    db = get_db()
    db.users.create_index([("username", ASCENDING)], unique=True)
    db.surveys.create_index([("slug", ASCENDING)], unique=True)
    db.surveys.create_index(
        [("ownerId", ASCENDING), ("status", ASCENDING), ("createdAt", -1)]
    )
    db.questions.create_index([("surveyId", ASCENDING), ("order", ASCENDING)])
    db.responses.create_index([("surveyId", ASCENDING), ("submittedAt", -1)])
    db.responses.create_index(
        [("surveyId", ASCENDING), ("respondent.userId", ASCENDING)]
    )
