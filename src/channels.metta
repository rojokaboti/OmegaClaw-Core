;configured at runtime:
(= (commchannel) (empty))

;Connect the selected channel via the upstream plugin runtime. initPlugins (called from the loop)
;registers each channel plugin (channels/*.py loadOmegaClawPlugin); commChannelConfig selects the
;channel by id and resolves its config from the CLI args.
(= (initChannels)
   (progn (log INFO "channels" "Initializing channels")
          (configure commchannel irc)
          (commChannelConfig (commchannel))))

;Receive the latest user message from the selected channel:
(= (receive)
   (commChannelReceive))

;Send a message to the selected channel. The &lastsend duplicate-send shield and (cut) stay here;
;the plugin does channel dispatch; the outgoing newline escape is applied before send.
!(change-state! &lastsend "")
(= (send $msg)
   (if (!= $msg (get-state &lastsend))
       (progn (change-state! &lastsend $msg)
              (let $safemsg (string-replace $msg  "\n" "\\n")
                   (let $temp (cut) (commChannelSend $safemsg)))) _))
