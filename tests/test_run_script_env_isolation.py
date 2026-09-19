from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_market_run_scripts_load_independent_env_files() -> None:
    expected = {
        "run_hk_once.sh": ".env.hk",
        "run_us_once.sh": ".env.us",
    }
    for script_name, env_name in expected.items():
        script = (PROJECT_ROOT / script_name).read_text(encoding="utf-8")
        assert f'ENV_FILE="$PWD/{env_name}"' in script
        assert 'export ENV_FILE' in script
        assert 'source "$ENV_FILE"' in script
        assert f"cp {env_name} .env" not in script
