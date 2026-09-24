import os

from app.config import _load_env


def test_env_file_in_project_root_is_loaded_regardless_of_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("FN_TEST_KEY", raising=False)
    (tmp_path / ".env").write_text("FN_TEST_KEY=hello-from-dotenv\n")
    other = tmp_path / "somewhere_else"; other.mkdir()
    monkeypatch.chdir(other)                       # launched from a different folder
    _load_env(tmp_path)
    assert os.environ["FN_TEST_KEY"] == "hello-from-dotenv"
    monkeypatch.delenv("FN_TEST_KEY")


def test_real_environment_variables_win_over_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("FN_TEST_KEY", "from-real-env")
    (tmp_path / ".env").write_text("FN_TEST_KEY=from-dotenv\n")
    _load_env(tmp_path)
    assert os.environ["FN_TEST_KEY"] == "from-real-env"
