"""应用工厂：注册蓝图、统一错误处理、静态页面与 /survey/<slug> 短链。"""
import os

from flask import Flask, jsonify, redirect, send_from_directory
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.db import ensure_indexes
from app.errors import ApiError

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


def create_app():
    app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")
    app.json.ensure_ascii = False

    from app.security import load_user

    @app.before_request
    def _auth():
        load_user()

    @app.get("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/survey/<slug>")
    def survey_link(slug):
        # 作业要求的短链形式 /survey/xxxxxx，转到填写页
        return redirect(f"/fill.html?slug={slug}")

    @app.get("/api/health")
    def health():
        try:
            get_db().command("ping")
            return jsonify({"status": "ok", "db": True})
        except PyMongoError:
            return jsonify({"status": "ok", "db": False}), 503

    from app.routes import auth, fill, questions, stats, surveys
    for bp in (auth.bp, surveys.bp, questions.bp, fill.bp, stats.bp):
        app.register_blueprint(bp)

    @app.errorhandler(ApiError)
    def _api_error(e):
        return jsonify({"error": e.message}), e.status

    @app.errorhandler(DuplicateKeyError)
    def _dup(e):
        return jsonify({"error": "数据冲突，请重试"}), 409

    @app.errorhandler(PyMongoError)
    def _mongo(e):
        app.logger.exception(e)
        return jsonify({"error": "数据库无法连接，请确认 MongoDB 已启动"}), 503

    @app.errorhandler(404)
    def _not_found(e):
        return jsonify({"error": "资源不存在"}), 404

    @app.errorhandler(405)
    def _method_not_allowed(e):
        return jsonify({"error": "请求方法不支持"}), 405

    try:
        ensure_indexes()
    except PyMongoError as e:
        app.logger.warning("索引创建失败（MongoDB 未启动？）：%s", e)

    return app


from app.db import get_db  # noqa: E402  （放末尾避免循环导入）
