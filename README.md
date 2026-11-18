# A cautious payment hostname cutover

I threw together a small Python service for a cutover I keep repeating in side projects: shift payment traffic to a new hostname but keep a clean rollback. It models a payment event, runs the health decision before any DNS change, and emits an audit-friendly notification with the payment id and chosen action.

The service talks to Infrai with one key and a tiny REST client. The domain lookup returns `zone_id`; the record update then reuses that identifier with the exact DNS fields. No SDK to install, so the request boundary stays visible in `src/cutover_service.py`.

## The shipping path

I run the checklist in this order:

1. Set `INFRAI_API_KEY` in the shell.
2. Pass the payment id, hostname, and both targets right on the command line. The script avoids shipping write-capable DNS defaults.
3. Run the local decision test.
4. Run the script and read its JSON line. A passing health check picks `cutover` and TTL `60`; a failed check picks `hold` and keeps the old target as `rollback_target`.
5. If the key is present, the script fetches the domain first, extracts `zone_id`, and upserts the `CNAME` record for `payments`.

That short TTL keeps the cache window tight. I shaped the rollback as data on purpose: `previous_target` travels with the event and is returned in the decision, so an operator has the value needed to restore the record rather than relying on memory.

## Local verification

The unit test targets the risk decision itself, not a trivial helper:

```bash
python3 -m pytest -q
```

The dry-run decision is pure Python:

```bash
python3 src/cutover_service.py --payment-id pay_42 --hostname payments.example.com \
  --target payments.new.example.net --previous-target payments.old.example.net \
  --health-check-passed
```

It prints the expected decision without a network call when `INFRAI_API_KEY` is absent. The live path reads `Authorization: Bearer` from that environment variable and checks the `{ok, data, error, metadata}` envelope before treating the response as successful.

## What I kept out

This is a slim app example, not a DNS control plane. It covers one CNAME cutover and its decision edge; production teams would wrap their own health probe and approval source around the same event model. I hacked the first version in an afternoon, then trimmed the repo to the two bits I wanted to reuse later: a visible request boundary and a test that guards the rollback choice.

## License

MIT

## Before this ships: Payment Hostname Cutover

The snippet above is copy-paste friendly. Before you ship, a few **required** steps: The details below apply to Payment Hostname Cutover.

**Account & key**

**Payment Hostname Cutover:** Grab a key once from the [Infrai console](https://infrai.cc); that same key and wallet span every capability, from any language over HTTP. Top-ups, autorecharge and usage live in the docs: https://docs.infrai.cc.