from app.config import AppConfig


def test_app_config_from_file_supports_org_import(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
anyshare:
  base_url: https://anyshare.example.test
  client_id: client-id
  client_secret: client-secret
  admin_account: admin
bisheng:
  base_url: https://bisheng.example.test
  jwt_secret: jwt-secret
  jwt_admin_token_version: 2
sync:
  max_objects_per_scan: 123
org_excel_path: /input/users.xlsx
""",
        encoding="utf-8",
    )

    config = AppConfig.from_file(config_path)

    assert config.anyshare.admin_account == "admin"
    assert config.bisheng.base_url == "https://bisheng.example.test"
    assert config.bisheng.jwt_secret == "jwt-secret"
    assert config.bisheng.jwt_admin_token_version == 2
    assert config.sync.max_objects == 123
    assert config.org_excel_path == "/input/users.xlsx"
