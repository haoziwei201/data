"""全局配置：全部支持环境变量覆盖，便于部署与测试。"""
import os

# MongoDB 连接串与库名
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "survey_db")

# 令牌签名密钥：生产环境务必通过环境变量指定
SECRET = os.environ.get("SECRET", "dev-secret-change-me")

# 登录令牌有效期（秒），默认 7 天
TOKEN_TTL = int(os.environ.get("TOKEN_TTL", str(7 * 24 * 3600)))
