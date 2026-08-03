import os
import yaml

def test_docker_compose_ultimate_bot_profile():
    compose_path = os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml")
    with open(compose_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        
    services = config.get("services", {})
    assert "crypto-bot" in services, "crypto-bot service must exist"
    crypto_bot = services["crypto-bot"]
    assert "profiles" in crypto_bot, "crypto-bot must have a profile defined"
    assert "ultimate-bot" in crypto_bot["profiles"], "crypto-bot must have 'ultimate-bot' profile to prevent bare up"

    collector = services["microstructure-collector"]
    assert collector["environment"]["MICROSTRUCTURE_MAINTENANCE_ENABLED"] == "false"

def test_deploy_scripts_no_bare_up_d():
    # Recursively check for 'docker compose up -d' without explicit service names
    root = os.path.join(os.path.dirname(__file__), "..")
    for dirpath, _, filenames in os.walk(root):
        # Skip git and venv and data_cache
        if ".git" in dirpath or "venv" in dirpath or "data_cache" in dirpath:
            continue
        for filename in filenames:
            if filename.endswith(".sh") or filename.endswith(".py"):
                path = os.path.join(dirpath, filename)
                with open(path, "r", encoding="utf-8") as f:
                    try:
                        content = f.read()
                        if "docker compose up -d" in content or "docker-compose up -d" in content:
                            # It must specify services
                            # Let's just find the exact line
                            for line in content.splitlines():
                                if "docker compose up -d" in line or "docker-compose up -d" in line:
                                    if line.strip().endswith("up -d"):
                                        assert False, f"Found bare 'docker compose up -d' in {filename}, must specify explicit service names!"
                    except UnicodeDecodeError:
                        pass
