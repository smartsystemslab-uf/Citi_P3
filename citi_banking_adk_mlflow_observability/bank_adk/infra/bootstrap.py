from .repository import repo

def bootstrap_runtime() -> None:
    repo.initialize()
