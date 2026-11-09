from sistema.config import config


def test_postgres_dsn_incluye_host_y_db():
    assert config.postgres_host in config.postgres_dsn
    assert config.postgres_db in config.postgres_dsn
