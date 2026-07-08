;configured at runtime:
(= (IRC_channel) (empty))
(= (IRC_server) (empty))
(= (IRC_port) (empty))
(= (IRC_user) (empty))
(= (commchannel) (empty))
(= (MM_URL) (empty))
(= (MM_CHANNEL_ID) (empty))
(= (TG_CHAT_ID) (empty))
(= (TG_POLL_TIMEOUT) (empty))
(= (SL_CHANNEL_ID) (empty))
(= (SL_POLL_INTERVAL) (empty))
(= (WS_URL) (empty))
(= (WS_TOKEN) (empty))

;Connect all the channels via the channel registry (Issue #9). Config is resolved from CLI args
;here (configure/argk); start_channel selects the channel and uses only its keys. Unknown
;commchannel -> mock (registry fallback), preserving the old else-branch behavior.
(= (initChannels)
   (progn (println! "Initializing channels")
          (configure commchannel irc)
          (configure IRC_channel ##omegaclaw)
          (configure IRC_server "irc.quakenet.org")
          (configure IRC_port 6667)
          (configure IRC_user omegaclaw)
          (configure TG_CHAT_ID "")
          (configure TG_POLL_TIMEOUT 20)
          (configure SL_CHANNEL_ID "")
          (configure SL_POLL_INTERVAL 60)
          (configure MM_URL "https://chat.singularitynet.io")
          (configure MM_CHANNEL_ID "8fjrmabjx7gupy7e5kjznpt5qh")
          (configure WS_URL "")
          (configure WS_TOKEN "")
          (py-call (channel_registry.start_channel (commchannel)
                    (IRC_channel) (IRC_server) (IRC_port) (IRC_user)
                    (TG_CHAT_ID) (TG_POLL_TIMEOUT) (SL_CHANNEL_ID) (SL_POLL_INTERVAL)
                    (MM_URL) (MM_CHANNEL_ID) (WS_URL) (WS_TOKEN)))))

;Receive the latest user message from the selected channel (unknown -> mock, in the registry):
(= (receive)
   (py-call (channel_registry.receive (commchannel))))

;Send a message to the selected channel. The &lastsend duplicate-send shield and (cut) stay here;
;the registry does channel dispatch + the outgoing newline escape.
!(change-state! &lastsend "")
(= (send $msg)
   (if (!= $msg (get-state &lastsend))
       (progn (change-state! &lastsend $msg)
              (let $temp (cut) (py-call (channel_registry.send (commchannel) $msg)))) _))

;Search the internet for some information:
(= (search $msg)
   (py-call (websearch.search $msg)))
