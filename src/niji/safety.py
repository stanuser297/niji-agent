import re

DANGEROUS = [
    r"rm\s+-[rfRF]+[^|;&]*\s+/\s*$",
    r"rm\s+-[rfRF]+\s+/(bin|boot|dev|etc|home|lib|proc|root|sbin|sys|usr|var)(\s|/|$)",
    r"\bmkfs(\.\w+)?\b",
    r"\bdd\b[^\n]*of=/dev/",
    r"\b(shutdown|reboot|poweroff|halt)\b",
    r"\b(chown|chmod)\b[^\n]*-R[^\n]*/\s*$",
    r"wget[^\n]*\|\s*(sudo\s+)?(ba)?sh",
    r"curl[^\n]*\|\s*(sudo\s+)?(ba)?sh",
    r"base64\s+(-d|--decode)[^\n]*\|\s*(ba)?sh",
    r":\(\)\s*\{\s*:\|:&\s*\};:",
]


def check_command(command: str):
    for pat in DANGEROUS:
        if re.search(pat, command):
            raise PermissionError(f"blocked dangerous pattern: /{pat}/")
