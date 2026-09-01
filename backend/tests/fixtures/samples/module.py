"""Sample Python module with classes, methods, and nested structures."""

import os
from typing import Optional


def parse_config(path: str) -> dict:
    """Parse a configuration file."""
    result = {}

    def parse_line(line: str) -> tuple:
        """Parse a single config line."""
        if "=" in line:
            key, val = line.split("=", 1)
            return key.strip(), val.strip()
        return line.strip(), ""

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                k, v = parse_line(line)
                result[k] = v
    return result


class User:
    """A user entity."""

    def __init__(self, name: str, email: str):
        self.name = name
        self.email = email

    def validate(self) -> bool:
        """Validate user data."""
        return "@" in self.email and len(self.name) > 0

    def get_display_name(self) -> str:
        """Return display name."""
        return self.name.upper()


class Outer:
    """Outer class with nested class."""

    class Inner:
        """Nested inner class."""

        def __init__(self, value: int):
            self.value = value

        def validate(self) -> bool:
            """Validate inner value."""
            return self.value > 0
