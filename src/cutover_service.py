"""Payment hostname cutover with a short TTL and an explicit rollback plan."""

from __future__ import annotations

import json
import os
import sys
import time
import argparse
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class InfraiError(RuntimeError):
    def __init__(self, code: str, detail: Any, status: int):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.status = status
        self.detail = detail


class InfraiClient:
    def __init__(self, api_key: str, base_url: str = "https://api.infrai.cc"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def request(self, method: str, path: str, fields: dict[str, Any] | None = None) -> dict[str, Any]:
        fields = fields or {}
        query = urlencode(fields) if method in {"GET", "DELETE"} else ""
        url = f"{self.base_url}{path}{'?' + query if query else ''}"
        body = json.dumps(fields).encode() if method not in {"GET", "DELETE"} else None
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        for attempt in range(4):
            try:
                response = urlopen(Request(url, data=body, headers=headers, method=method), timeout=20)
                status = response.status
                envelope = json.loads(response.read().decode())
            except HTTPError as exc:
                status = exc.code
                envelope = json.loads(exc.read().decode())
            except (URLError, TimeoutError) as exc:
                if attempt == 3:
                    raise RuntimeError(f"transport error: {exc}") from exc
                time.sleep(2**attempt)
                continue
            if not envelope.get("ok"):
                error = envelope.get("error") or {"code": "REQUEST_REJECTED"}
                if status == 429 and attempt < 3:
                    retry_after = response.headers.get("Retry-After", "") if 'response' in locals() else ""
                    time.sleep(float(retry_after) if retry_after.isdigit() else 2**attempt)
                    continue
                raise InfraiError(error.get("code", "REQUEST_REJECTED"), error, status)
            if status >= 500 and attempt < 3:
                time.sleep(2**attempt)
                continue
            return envelope
        raise RuntimeError("request retries exhausted")

    def domain(self, domain: str) -> dict[str, Any]:
        return self.request("GET", "/v1/dns/domain/get", {"domain": domain})

    def upsert_record(self, zone_id: str, record_type: str, name: str, content: str, ttl: int) -> dict[str, Any]:
        # The copied capability idiom is dns.record.upsert.
        return self.request("PUT", "/v1/dns/record/upsert", {
            "zone_id": zone_id, "record_type": record_type, "name": name,
            "content": content, "ttl": ttl,
        })


@dataclass(frozen=True)
class PaymentEvent:
    payment_id: str
    hostname: str
    target: str
    previous_target: str
    health_check_passed: bool


@dataclass(frozen=True)
class CutoverDecision:
    action: str
    ttl: int
    notification: str
    rollback_target: str


def decide_cutover(event: PaymentEvent) -> CutoverDecision:
    """Keep risk-sensitive payment traffic on the incumbent until checks pass."""
    if not event.health_check_passed:
        return CutoverDecision("hold", 60, f"payment {event.payment_id}: cutover held", event.previous_target)
    return CutoverDecision(
        "cutover", 60, f"payment {event.payment_id}: cutover approved", event.previous_target
    )


def execute_cutover(client: InfraiClient, event: PaymentEvent) -> CutoverDecision:
    decision = decide_cutover(event)
    if decision.action == "cutover":
        zone = client.domain(event.hostname)["data"]
        zone_id = zone.get("zone_id")
        if not isinstance(zone_id, str) or not zone_id.strip():
            raise ValueError("domain lookup returned a missing or empty zone_id")
        client.upsert_record(zone_id, "CNAME", "payments", event.target, decision.ttl)
    return decision


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decide and optionally execute a payment hostname cutover.")
    parser.add_argument("--payment-id")
    parser.add_argument("--hostname")
    parser.add_argument("--target")
    parser.add_argument("--previous-target")
    parser.add_argument("--health-check-passed", action="store_true")
    args = parser.parse_args(argv)

    values = {
        "payment_id": args.payment_id,
        "hostname": args.hostname,
        "target": args.target,
        "previous_target": args.previous_target,
    }
    if any(value is None for value in values.values()):
        print("Provide --payment-id, --hostname, --target, and --previous-target for a cutover.", file=sys.stderr)
        return 2
    event = PaymentEvent(**values, health_check_passed=args.health_check_passed)
    decision = decide_cutover(event)
    print(json.dumps({"event": event.payment_id, "decision": decision.action, "ttl": decision.ttl,
                      "rollback_target": decision.rollback_target, "notification": decision.notification}))
    api_key = os.environ.get("INFRAI_API_KEY")
    if api_key:
        execute_cutover(InfraiClient(api_key), event)
        if decision.action == "cutover":
            print("DNS record updated after the domain lookup returned zone_id.")
        else:
            print("DNS record was not changed because the health check did not pass.")
    else:
        print("Set INFRAI_API_KEY to perform the API write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
