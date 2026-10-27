import os
import subprocess
import sys
from pathlib import Path


def test_import_does_not_initialize_db_or_write_logs(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = '''
from pathlib import Path
import sqlalchemy
def forbidden(*args, **kwargs):
    raise AssertionError("import opened a database")
sqlalchemy.create_engine = forbidden
import goodprice.main
import goodprice.__main__
assert callable(goodprice.main.create_app)
assert not Path("data").exists()
'''
    env = {**os.environ, "PYTHONPATH": str(root), "DATABASE_URL": "sqlite:///:memory:"}
    result = subprocess.run(
        [sys.executable, "-c", source], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_explicit_app_uses_only_temporary_store(base_settings, session_factory):
    from goodprice.main import build_app

    app = build_app(settings=base_settings, session_factory=session_factory, with_scheduler=False)
    assert app.state.session_factory is session_factory
    assert app.state.settings_service.get().database_url == base_settings.database_url


def test_benchmark_is_offline_and_uses_temporary_database(tmp_path):
    import json

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/benchmark_pipeline.py"),
         "--rounds", "1", "--output", str(tmp_path)],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "benchmark.json").read_text(encoding="utf-8"))
    assert report["dataset"] == "synthetic-30"
    assert report["results"][0]["found"] == 30
    assert report["external_network_requests"] == 0

def test_staged_benchmark_preserves_coverage_and_warm_model_cache(tmp_path):
    import json
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / "scripts/benchmark_stages.py"),
        "--rounds", "1", "--items", "3", "--output", str(tmp_path)], cwd=root, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "stages.json").read_text(encoding="utf-8"))
    assert report["external_network_requests"] == 0
    for mode in ("cold", "warm"):
        for engine in ("serial", "staged"):
            assert report[engine][mode]["results"][0]["verified_offers"] == 3
    assert report["staged"]["warm"]["results"][0]["model_attempts"] == 0
