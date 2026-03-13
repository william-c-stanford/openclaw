from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import Config, build_config_from_args


def test_config_defaults() -> None:
    config = Config()
    assert config.dataset_name == "MariusHobbhahn/swe-bench-verified-mini"
    assert config.max_workers == 4
    assert config.thinking_level == "low"


def test_config_run_id_auto_generated() -> None:
    config = Config()
    assert config.run_id.startswith("swe-mini-")


def test_config_predictions_path_under_output() -> None:
    config = Config()
    assert config.run_id in str(config.predictions_path)


def test_args_defaults() -> None:
    config = build_config_from_args([])
    assert config.dataset_name == "MariusHobbhahn/swe-bench-verified-mini"


def test_args_workers() -> None:
    config = build_config_from_args(["--workers", "2"])
    assert config.max_workers == 2


def test_args_instance_filter() -> None:
    config = build_config_from_args(["--instance-filter", "sympy__*"])
    assert config.instance_filter == "sympy__*"


def test_args_skip_eval() -> None:
    config = build_config_from_args(["--skip-eval"])
    assert config.skip_eval is True


def test_args_dry_run() -> None:
    config = build_config_from_args(["--dry-run"])
    assert config.dry_run is True


def test_args_run_id() -> None:
    config = build_config_from_args(["--run-id", "my-run"])
    assert config.run_id == "my-run"
