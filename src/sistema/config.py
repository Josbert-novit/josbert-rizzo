import os
from pathlib import Path


class Config:
    mqtt_host = os.getenv("MQTT_HOST", "broker")
    mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
    historian_url = os.getenv("HISTORIAN_URL", "http://sim:8100")
    media_dir = Path(os.getenv("MEDIA_DIR", "/data/notas"))

    postgres_host = os.getenv("POSTGRES_HOST", "postgres")
    postgres_port = int(os.getenv("POSTGRES_PORT", "5432"))
    postgres_db = os.getenv("POSTGRES_DB", "sistema")
    postgres_user = os.getenv("POSTGRES_USER", "sistema")
    postgres_password = os.getenv("POSTGRES_PASSWORD", "sistema")

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


config = Config()
