from bank_adk.settings import settings

def test_data_dir_exists():
    assert settings.data_dir.exists()
