;FreeCiv benchmark tools (Issue #6).
;
;freeciv-observe fetches the current llm_optimized game state from the freeciv-llm proxy and
;returns it as PLN sentences (facts with (stv f c) truth values) the agent can reason over.
;
;freeciv-action validates a candidate action (JSON string) against the current state and the
;server's legal_actions, and submits it ONLY if legal. Illegal actions are refused (structured
;deny, logged) and never reach action_submit. Endpoints/credentials come from the environment
;(FREECIV_PROXY_URL / FREECIV_WS_URL / FREECIV_API_TOKEN / FREECIV_GAME_ID / FREECIV_PLAYER_ID).

(= (freeciv-observe)
   (py-call (freeciv_tool.observe)))

(= (freeciv-action $json)
   (py-call (freeciv_tool.act $json)))
