"""Disable TLS certificate verification for all httpx requests, process-wide.

The customer's AnyShare/BISHENG endpoints use internal/self-signed
certificates this environment doesn't trust, so requests otherwise fail with
SSLCertVerificationError. httpx has no global "don't verify" switch, but
every request path — explicit `httpx.Client(...)` instances *and* the
module-level `httpx.get()`/`post()`/`request()` helpers (which construct a
`Client(..., verify=verify, ...)` internally) — funnels through
`httpx.Client.__init__`. Patching that one constructor covers every call
site in the app, present and future, without threading `verify=False`
through dozens of call sites individually.

Imported once, for its side effect, at the top of app/config.py — which
every entrypoint script imports before making any request.
"""

import httpx

_orig_client_init = httpx.Client.__init__


def _insecure_client_init(self, *args, **kwargs):
    kwargs["verify"] = False
    _orig_client_init(self, *args, **kwargs)


httpx.Client.__init__ = _insecure_client_init
