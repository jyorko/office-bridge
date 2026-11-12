"""Export the live local runtime schema without any run or message data."""

import json

import requests
from _bootstrap import ROOT


def order_like(value, previous):
    if isinstance(value, dict) and isinstance(previous, dict):
        keys = [key for key in previous if key in value] + [
            key for key in value if key not in previous
        ]
        return {key: order_like(value[key], previous.get(key)) for key in keys}
    if isinstance(value, list) and isinstance(previous, list):
        return [
            order_like(item, previous[i] if i < len(previous) else None)
            for i, item in enumerate(value)
        ]
    return value


def main():
    destination = ROOT / "docs/api/openapi.json"
    with requests.Session() as client:
        client.trust_env = False
        response = client.get("http://127.0.0.1:8766/openapi.json", timeout=15)
        response.raise_for_status()
        schema = response.json()
    if schema.get("openapi") != "3.1.0" or not isinstance(schema.get("paths"), dict):
        raise SystemExit("The runtime did not return an OpenAPI document.")
    if destination.exists():
        schema = order_like(schema, json.loads(destination.read_text(encoding="utf-8")))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Exported {len(schema['paths'])} paths to {destination.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
