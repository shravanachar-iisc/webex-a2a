# Synthetic A2A demo

A standalone A2A 1.0 JSON-RPC agent for Webex beta onboarding tests. It uses
`a2a-sdk[http-server]==1.3.0`, returns a fixed synthetic answer, and has no LLM,
database, external API calls, or user identity. It is not production security.

- Public discovery: `GET /.well-known/agent-card.json`
- Public health: `GET /health`
- Authenticated A2A: `POST /a2a`, with `Authorization: Bearer <key>` and
  `A2A-Version: 1.0`. Streaming uses `SendStreamingMessage` and SSE; blocking
  requests use `SendMessage`.
- Sending exactly `clarify` (case-insensitive, surrounding whitespace ignored)
  returns `TASK_STATE_INPUT_REQUIRED`. Send another message with the returned
  `taskId` and `contextId` to finish the same task.

Tasks and their message histories are stored **only in memory** and disappear on
restart, redeploy, or spin-down. Every holder of the shared key can access
stored tasks; there is no per-user isolation. Use synthetic data only. The app
does not log keys or message bodies, and disables SDK and SSE diagnostics that
could include request data. Access logs are disabled.

## Run locally

Use Python 3.13 (the pinned version is 3.13.5):

```sh
python3.13 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
export A2A_POC_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python -m pytest -q
python agent.py
```

The key must be a nonempty Bearer-token-safe string; keep it private and do not
commit it. The server binds to `0.0.0.0` and defaults to port 8000, so it may
also be reachable by other devices on your network. Restrict access with your
firewall. Local HTTP is only for testing; use HTTPS for any remote access.
The advertised local URL defaults to `http://localhost:8000`, or the local
`PORT` if set.

In another shell, set the same `A2A_POC_API_KEY` without exposing it in shell
history, then:

```sh
curl -s http://localhost:8000/health
curl -s http://localhost:8000/.well-known/agent-card.json
curl -s http://localhost:8000/a2a \
  -H "Authorization: Bearer $A2A_POC_API_KEY" \
  -H 'A2A-Version: 1.0' -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"messageId":"m1","role":"ROLE_USER","parts":[{"text":"hello"}]}}}'
```

For SSE, use `curl -N` and change `SendMessage` to `SendStreamingMessage`.
Use only a synthetic message. The SDK treats requests without `A2A-Version:
1.0` as the older protocol and rejects them.

## Render Free setup

Do this only after explicitly approving publication and deployment:

1. Make this code available to Render, then create a **Web Service**, select the
   repository, Python runtime, and **Free** instance type.
2. Set build command `pip install -r requirements.txt` and start command
   `python agent.py`. The `.python-version` file selects Python 3.13.5.
3. Set `A2A_POC_API_KEY` as a secret environment variable to a strong,
   independently generated key. Render supplies `PORT` and
   `RENDER_EXTERNAL_URL`; the latter is advertised in the Agent Card.
   Optionally set the health-check path to `/health`.
4. Deploy and verify both public URLs and an authenticated A2A call over the
   Render HTTPS URL. Never share the key in URLs or screenshots.

Free instances spin down after inactivity and can take about a minute to wake;
their in-memory tasks are lost on spin-down. Consult
[Render's Free limitations](https://render.com/docs/free) for current limits.

## Webex beta registration

Per [Webex's onboarding guide](https://developer.webex.com/create/docs/onboard-your-agent),
enroll in the A2A beta and deploy an HTTPS-reachable server first. In the
[Webex Developer Portal](https://developer.webex.com/my-apps/new), choose
**Create an Agentic App**, select **A2A**, and enter only the Render HTTPS base
URL (no path). Click **Validate**; the portal discovers the Agent Card,
transport, and API-key scheme. Review the unique name and description, choose a
logo, then click **Add Agentic App**. The server URL and discovered transport
and auth type are not editable after registration.

Registration validates discovery; it does **not** prove that a Webex consumer
can invoke the agent, can supply the expected Bearer header, or establishes an
employee's identity. Test invocation separately and use a real identity system
for any non-demo scenario.
