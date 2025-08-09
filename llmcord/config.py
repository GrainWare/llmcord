from __future__ import annotations

from typing import Any
import yaml


def get_config(filename: str = "config.yaml") -> dict[str, Any]:
    with open(filename, encoding="utf-8") as file:
        return yaml.safe_load(file)


__all__ = ["get_config"]
