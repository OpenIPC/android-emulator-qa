# tinyCam ONVIF auth failure — unprefixed WSSE namespace (2026-08-23)

**Report:** tinyCam Monitor PRO prompts for a password in a loop against an
OpenIPC camera ("Camera authorization required" / "Video failed (Check username
and password)"), and the earlier merged fix (widgetii/majestic PR #357,
`9015d295`) did not resolve it.

**Filed:** [widgetii/majestic#397](https://github.com/widgetii/majestic/issues/397)

## Root cause

majestic's ONVIF WSSE parser (`src/onvif/wsse.c` `find_username_token`) only
matches namespace-**prefixed** `<wsse:Security>`/`<wsse:UsernameToken>`. tinyCam
sends the semantically-identical **default-namespace** form
`<Security xmlns="…secext…">` with unprefixed `<Username>`/`<Password>` children.
mxml's `xmlns` pseudo-attribute match resolves only prefixed elements, so the
`Username`/`Password`/`Nonce` lookups return NULL, the token is treated as
absent, and every request is rejected as unauthenticated — regardless of correct
credentials, `onvif.password`, or PR #357.

## Proof (live, camera default `onvif.password` empty, correct `root`/`123456`)

| WSSE form sent (identical credentials) | majestic |
|---|---|
| Unprefixed `<Security xmlns=…>` PasswordText | ❌ FailedAuthentication |
| Unprefixed `<Security xmlns=…>` PasswordDigest — **tinyCam's exact form** | ❌ FailedAuthentication |
| Prefixed `<wsse:Security>` PasswordText | ✅ 200 OK |
| Prefixed `<wsse:Security>` PasswordDigest *(with onvif.password set)* | ✅ 200 OK |
| HTTP Basic | ✅ 200 OK |

The only variable between the ❌ and ✅ WSSE rows is prefix-vs-default namespace.
tinyCam sends **only** PasswordDigest (unprefixed), retried ~35×, never falling
back to PasswordText or HTTP Basic.

Two independent blockers for tinyCam specifically: (1) the namespace parsing
bug, and (2) PasswordDigest additionally needs cleartext `onvif.password`.
Setting `onvif.password` alone does **not** help — confirmed live.

## How it was found

Android emulator (KVM) drove tinyCam v18.1.2 via adb against an OpenIPC camera
(ssc33x / imx335, majestic `master+c5bf196`, 2026-08-14). Traffic captured with
the host-side logging relay (`harness/relay.py`), since the emulator's
`-tcpdump` does not capture the slirp-NAT'd camera path. tinyCam's captured
request is in `2026-08-23-tinycam-request.xml`; the machine-readable summary is
`2026-08-23-tinycam-wsse-verdict.json`.
