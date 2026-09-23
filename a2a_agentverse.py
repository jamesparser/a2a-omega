"""Agentverse (Fetch.ai / SingNET) mailbox transport for the A2A hub.

Optional SDK-backed module. The hub only imports this lazily when
A2A_TRANSPORT=agentverse, so a box that has NOT installed uagents (e.g. a VPS
running only the hub) keeps working with the AgentMail default.

What it is
----------
Every uAgents agent can be given a **Mailbox** on Agentverse: it runs locally
(execution stays on your machine) while Agentverse stores inbound messages and
polls them back when the agent is online. Outbound delivery to a peer mailbox is
a signed envelope POSTed to the Agentverse mailbox-submit endpoint with the
operator's Agentverse API key (``Authorization: Bearer <key>``).

There is NO on-chain funding / gas step on the mailbox path: the agent only
carries a local Ed25519 identity (derived from a seed phrase) which signs the
registration challenge and the envelope. The on-chain "register on almanac
contract" step is executed by the Agentverse platform backend against your API
key, not by your agent. The API key itself is free to mint from a Fetch account
(accounts.fetch.ai, Google-verified).

Config (env, none committed)
---------------------------
    A2A_AGENTVERSE_API_KEY   the operator Agentverse API key (av_...)
    A2A_AGENTVERSE_KEY_SITE  vault site holding that key (if not in env)
    A2A_AGENTVERSE_BASE      default https://agentverse.ai
    A2A_AGENTVERSE_SEED      hub-side identity seed phrase (used to sign envelopes)
    per-peer in peers.json:  "agentverse_address"  the peer's mailbox address
                             "agentverse_seed"     peer seed phrase (single-node testing)

Backward compatible: when uagents is missing, or a peer has no Agentverse
address, or the API key is absent, `av_send` returns {"error": ...} and the hub
falls through to AgentMail, then MailSlurp. No exception is raised from a
misconfigured Agentverse.
"""
import json
import os
import time
import secrets
import urllib.request
import urllib.error

_UAGENTS = None
_UAGENTS_ERR = ""


def _uagents():
    """Lazy import of the uagents SDK so the hub stays stdlib-importable."""
    global _UAGENTS, _UAGENTS_ERR
    if _UAGENTS is not None:
        return _UAGENTS
    try:
        import uagents  # noqa: F401
        _UAGENTS = "ok"
    except Exception as e:  # noqa: BLE001 - optional dependency
        _UAGENTS = ""
        _UAGENTS_ERR = f"{type(e).__name__}: {e}"
    return _UAGENTS


def _api_key():
    """Resolve the Agentverse API key (env first, then vault). Never prints it."""
    key = os.environ.get("A2A_AGENTVERSE_API_KEY", "").strip()
    if key:
        return key
    site = os.environ.get("A2A_AGENTVERSE_KEY_SITE", "")
    if site:
        try:
            import importlib.util
            here = os.path.dirname(os.path.abspath(__file__))
            spec = importlib.util.spec_from_file_location("a2a_hub", os.path.join(here, "a2a_hub.py"))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "_vault_key"):
                    return mod._vault_key(site)
        except Exception:
            return ""
    return ""


def _identity(seed):
    """Local Ed25519 identity from a seed phrase (uagents SDK)."""
    from uagents_core.identity import Identity
    return Identity.from_seed(seed, 0)


def av_send(peer, text, api_key=None, peer_address=None, sender_seed=None, base_url=None):
    """Send `text` to a peer's Agentverse mailbox via a signed envelope.

    Returns {"ok": True, "task_id": ...} on success, or {"error": "..."} when
    Agentverse is unavailable / misconfigured -- the hub then falls through to
    the next transport. `text` may be a str or a JSON-serialisable object.
    """
    sdk = _uagents()
    if not sdk:
        return {"error": f"agentverse unavailable: uagents SDK not installed ({_UAGENTS_ERR})"}
    api_key = api_key or _api_key()
    if not api_key:
        return {"error": "agentverse unavailable: no Agentverse API key "
                         "(set A2A_AGENTVERSE_API_KEY or A2A_AGENTVERSE_KEY_SITE)"}
    if not peer_address:
        return {"error": f"agentverse: peer {peer!r} has no agentverse_address in peers.json"}

    sender_seed = sender_seed or os.environ.get("A2A_AGENTVERSE_SEED", "a2a-omega-hub")
    base_url = base_url or os.environ.get("A2A_AGENTVERSE_BASE", "https://agentverse.ai")
    submit = f"{base_url}/v2/agents/mailbox/submit"

    # A2A chat payload schema digest (stable across the fleet).
    schema_digest = "0x" + "a2a".encode().hex().ljust(64, "0")[:64]
    protocol_digest = "0x" + "0" * 64
    payload_body = text if isinstance(text, str) else json.dumps(text, default=str)

    try:
        import uuid
        from uagents_core.envelope import Envelope
        ident = _identity(sender_seed)
        env = Envelope(
            version=1,
            sender=ident.address,
            target=peer_address,
            session=uuid.uuid4(),
            schema_digest=schema_digest,
            protocol_digest=protocol_digest,
            expires=int(time.time()) + 3600,
            nonce=int(secrets.token_hex(4), 16),
        )
        env.encode_payload(payload_body)
        env.sign(ident)
        body = env.model_dump_json()
    except Exception as e:  # noqa: BLE001 - degrade to error, hub falls back
        return {"error": f"agentverse envelope build/sign failed: {type(e).__name__}: {e}"}

    req = urllib.request.Request(
        submit, data=body.encode(), method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "a2a-omega/1.1",
        },
    )
    try:
        r = urllib.request.urlopen(req, timeout=15)
        r.read()
        return {"ok": True, "task_id": f"av-{int(time.time())}", "peer": peer}
    except urllib.error.HTTPError as e:
        return {"error": f"agentverse HTTP {e.code}: {e.read().decode(errors='replace')[:160]}"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"agentverse send failed: {type(e).__name__}: {e}"}
