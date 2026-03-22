LICENSES = [
    {"id": "MIT", "name": "MIT License", "spdx_id": "MIT", "url": "https://opensource.org/licenses/MIT", "permissive": True},
    {"id": "Apache-2.0", "name": "Apache License 2.0", "spdx_id": "Apache-2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0", "permissive": True},
    {"id": "GPL-3.0", "name": "GNU GPL v3", "spdx_id": "GPL-3.0", "url": "https://www.gnu.org/licenses/gpl-3.0", "permissive": False},
    {"id": "BSD-3-Clause", "name": "BSD 3-Clause", "spdx_id": "BSD-3-Clause", "url": "https://opensource.org/licenses/BSD-3-Clause", "permissive": True},
    {"id": "ISC", "name": "ISC License", "spdx_id": "ISC", "url": "https://opensource.org/licenses/ISC", "permissive": True},
    {"id": "MPL-2.0", "name": "Mozilla Public License 2.0", "spdx_id": "MPL-2.0", "url": "https://www.mozilla.org/en-US/MPL/2.0/", "permissive": True},
    {"id": "CC0-1.0", "name": "CC0 1.0", "spdx_id": "CC0-1.0", "url": "https://creativecommons.org/publicdomain/zero/1.0/", "permissive": True},
    {"id": "Unlicense", "name": "The Unlicense", "spdx_id": "Unlicense", "url": "https://unlicense.org/", "permissive": True},
]


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    license_id = params.get("license") or params.get("id")
    if license_id:
        license_id = str(license_id).strip()
        for lic in LICENSES:
            if lic["id"] == license_id or lic["spdx_id"] == license_id:
                return {"status": "ok", "error": None, "data": lic}
        return _error("License not found")
    data = {"licenses": LICENSES, "count": len(LICENSES)}
    return {"status": "ok", "error": None, "data": data}
