import os

import yaml


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_yaml(name):
    path = os.path.join(PROJECT_ROOT, "config", name)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_settings():
    return load_yaml("settings.yaml")


def load_keywords():
    return load_yaml("keywords.yaml")


def load_seeds():
    data = load_yaml("seeds.yaml")
    return data.get("seed_accounts", [])


def project_path(*parts):
    return os.path.join(PROJECT_ROOT, *parts)


def ensure_dirs(settings):
    d = os.path.dirname(project_path(settings["app"]["db_path"]))
    os.makedirs(d, exist_ok=True)
    os.makedirs(project_path(settings["app"].get("output_dir", "output")), exist_ok=True)
    os.makedirs(project_path(settings["app"].get("logs_dir", "logs")), exist_ok=True)
