import yaml
import os

def runtime_domain(agent_name: str) -> str:
    """Determines the operational domain of an agent at runtime."""
    if agent_name.startswith("front_"):
        return "front_office"
    elif agent_name.startswith("mid_"):
        return "mid_office"
    elif agent_name.startswith("back_"):
        return "back_office"
    elif agent_name.startswith("aml_"):
        return "aml_office"
    else:
        return "shared"


MANIFEST_DIR = "manifests/"

def load_manifest(filename: str):
    filepath = os.path.join(MANIFEST_DIR, filename)
    if os.path.exists(filepath):
        with open(filepath, 'r') as file:
            return yaml.safe_load(file)
    return []

