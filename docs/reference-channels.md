# Reference — Channels

Channels are the I/O surface the agent uses to talk to the outside world. Adapters live in `channels/`; the MeTTa side (`src/channels.metta`) is a thin facade that resolves config from CLI args and delegates start/receive/send to the Python channel registry (`src/channel_registry.py`, Issue #9).

## The adapter contract

Each adapter exposes:

| Function | Purpose |
|---|---|
| `start_<name>(...)` | Called once from `initChannels`. Opens sockets / spawns listener threads as needed. |
| `getLastMessage()` | Returns the next unread inbound message as a string. Returns `""` if none. |
| `send_message(str)` | Posts an outbound message. |

The MeTTa side no longer hardcodes a per-channel `if` chain. `commchannel` (and the resolved
config keys) are passed straight to the registry, which dispatches by name:

```metta
(= (receive)
   (py-call (channel_registry.receive (commchannel))))
```

`send` and `initChannels` delegate the same way (`channel_registry.send` / `.start_channel`). The
registry maps each name to a `Channel` object (`src/channel_registry.py`); adding a channel is one
`register(...)` call rather than editing three branches. An unknown `commchannel` resolves to
`channels/mock.py`, the in-process test channel used when no real channel is selected.

## `channels/irc.py`

IRC adapter with simple one-time-secret authentication.

- `start_irc(channel, server, port, user)` — connect and join.
- Inbound traffic is filtered to the first user who types `auth <one-time-secret>`. All other speakers are ignored.
- Uses QuakeNet (`irc.quakenet.org`) by default.

## `channels/mattermost.py`

Mattermost adapter using a bot token.

- `start_mattermost(url, channel_id)` — connect to a Mattermost instance.
- Requires `MM_BOT_TOKEN` environment variable.

## `channels/telegram.py`

Telegram adapter using Bot API long polling.

- `start_telegram(chat_id, poll_timeout)` — starts a poll loop.
- `TG_CHAT_ID` is optional; if empty, the adapter can auto-bind to the first valid inbound chat.
- Outbound messages are chunked to Telegram-safe lengths.

## `channels/slack.py`

Slack adapter using Slack Web API polling.

- `start_slack(channel_id, poll_interval)` — starts a poll loop.
- `SL_CHANNEL_ID` is optional.
- The bot user must already be invited to the target channel.
- If `SL_CHANNEL_ID` is empty, the adapter auto-binds to the first channel where auth succeeds.
- Adapter respects Slack `Retry-After` backoff on HTTP 429 and enforces a minimum 60s poll interval.
- Uses the same one-time `auth <secret>` ownership gate as the other adapters.

## `channels/wschat.py`

Minimal JSON chat adapter over a WebSocket connection. Selected with `commchannel=websocket` — the Python module is `wschat`, exposing `start_websocket` / `stop_websocket` alongside the usual `getLastMessage` / `send_message`.

- `start_websocket(ws_url, ws_token)` — connect and spawn the listener thread. URL and token are read from `WS_URL` / `WS_TOKEN`, or passed directly.
- **Fail-closed (this fork):** because inbound frames drive the agent (the channel is effectively a control plane) and — unlike the polling adapters — there is no in-frame one-time `auth <secret>` gate, the channel registry **requires both a non-empty `WS_URL` and a non-empty `WS_TOKEN`** when `commchannel=websocket`. If either is missing the registry **declines to start** the channel (reported as `CHANNEL-DISABLED:websocket`) rather than connecting to / advertising an unauthenticated endpoint; OmegaClaw itself still starts and continues without an active WebSocket connection.
- `stop_websocket()` — stop the listener thread and close the socket.
- Requires the `websockets` Python package.
- `WS_TOKEN` is sent as an `Authorization: Bearer <token>` header, establishing trust with the endpoint (which server the agent connects to, authenticated by the bearer token).
- Reconnects automatically with exponential backoff (1s → 30s, ±20% jitter) and is safe to start once at process startup.

### Frame protocol

All frames are UTF-8 JSON objects with a `type` field; unknown types are logged and ignored.

| Direction | `type` | Payload |
|---|---|---|
| server → client | `user_message` | `{seq, text}` — a new inbound message. `seq` is a server-assigned, monotonically increasing integer used for ordering and dedup. |
| server → client | `ack` | `{seq, client_seq}` — acknowledges a previously sent `agent_message`. Informational; logged only. |
| server → client | `error` | `{code, message}` — server-side error. Logged; the connection is left open. |
| client → server | `agent_message` | `{client_seq, text}` — an outbound message. `client_seq` is a client-generated UUID idempotency key so the server can dedupe retries after reconnect. |
| client → server | `resume` | `{last_seen_seq}` — sent on every (re)connect so the server can replay any `user_message` with `seq > last_seen_seq` (null on the first connect). |

### Delivery semantics

- Inbound messages buffer in a bounded inbox (256 entries). `getLastMessage` drains it, joins pending texts with `" | "`, and advances `last_seen_seq`.
- Outbound messages produced while disconnected queue in a bounded outbox (100 entries) and flush after the next successful connect, before any new inbound traffic is processed.
- Duplicate `user_message` frames (`seq <= last_seen_seq`, or already buffered) are dropped, so server replays after `resume` are idempotent.

## `channels/websearch.py`

Not a communication channel in the `send`/`receive` sense — this is the backend for the `search` skill. Exposes `search(query)`.

## Adding a new channel

See [tutorial-04-adding-a-channel.md](./tutorial-04-adding-a-channel.md).

## Related reference

- [reference-skills-communication.md](./reference-skills-communication.md) — the MeTTa surface (`send`, `receive`, `search`).
- [reference-configuration.md](./reference-configuration.md) — channel parameters.
