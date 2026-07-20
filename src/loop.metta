(= (maxNewInputLoops) (empty))
(= (maxWakeLoops) (empty))
(= (sleepInterval) (empty))
(= (LLM) (empty))
(= (provider) (empty))
(= (maxOutputToken) (empty))
(= (reasoningMode) (empty))
(= (wakeupInterval) (empty))
(= (logConfigPath) (empty))
(= (memoryDirectory) (empty))
(= (spamShield) (empty)) ; TODO: this parameter is considered deprecated

(= (initLoop)
   (progn (configure maxNewInputLoops 50) ;20
          (configure maxWakeLoops 1)
          (configure spamShield False)
          (configure sleepInterval 1) ;10
          (configure LLM gpt-5.4)
          (configure provider Anthropic) ;Anthropic or OpenAI or ASICloud or ASIOne or Test
          (configure maxOutputToken 6000)
          (configure reasoningMode medium)
          (configure wakeupInterval 600) ;600=10 minutes
          (configure logConfigPath "")
          (configure memoryDirectory ./)
          (change-state! &prevmsg "")
          (change-state! &lastresults "")
          (change-state! &loops (maxNewInputLoops))
          (py-call (logger.setup_logging (logConfigPath)))
          ))

(= (initKnowledge)
   (progn (log INFO "loop" "Initializing knowledge base")
          (if (== (embeddingprovider) OpenAI)
               (log INFO "loop" (py-call (rag.init_knowledge "OpenAI")))
               (log INFO "loop" (py-call (rag.init_knowledge "Local"))))))

(= (getContext)
   (string-safe (py-str ("PROMPT: " (getPrompt (provider)) " SKILLS: " (getSkills)
                         " " (py-call (action_protocol.output_format_block)) " "
                         " " (py-call (skill_loader.catalogue_block)) " "
                         " " (py-call (plugin_registry.catalogue_block)) " "
                         " SAVE_PERMANENT_FILES_DIR: " (memoryDirectory)
                         " LAST_SKILL_USE_RESULTS: " (last_chars (get-state &lastresults) (maxFeedback)) " HISTORY: " (getHistory) " TIME: " (get_time_as_string)))))

; Structured error recovery events (Issue #10). The recorder runs a py-call that
; emits a machine-readable trace_error event under the current iteration's
; trace_id and returns the concise, category-specific repair hint that is stored
; into &error (and thus fed back to the model as ERROR_FEEDBACK) instead of the
; old opaque symbolic token. It is evaluated ONLY inside the (Error ...) branch
; of HandleError, so a successful action never records a spurious error.
(= (recordErrorHint parse_error $cmd) (py-call (errors.record_parse_error $cmd)))
(= (recordErrorHint tool_runtime_error $cmd) (py-call (errors.record_runtime_error (repr $cmd))))

(= (HandleError $kind $cmd $sexpr)
   (case $sexpr (((Error $a $b) (let $hint (recordErrorHint $kind $cmd)
                                     (let $new (append (get-state &error) (($hint $cmd)))
                                          (change-state! &error $new))))
                 ($else $sexpr))))

(= (omegaclaw)
   (omegaclaw 1))
(= (omegaclaw $k)
   (progn (if (== $k 1) (progn (initLoop)
                               (applySecurityPolicy)
                               (initMemory)
                               (initKnowledge)
                               (initPlugins)
                               (initChannels)
                               (llmProviderConfig (provider))
                               (py-call (tracing.begin_session)))
                        (change-state! &loops (- (get-state &loops) 1)))
          (let $prompt (getContext)
               (progn (log INFO "loop" (---------iteration $k))
                      (py-call (tracing.begin_iteration $k))
                      (let* (($msgrcv (string-safe (repr (receive))))
                             ($msgnew (prog1 (and (> (string_length $msgrcv) 0) (!= $msgrcv (get-state &prevmsg)))
                                             (if (> (string_length $msgrcv) 0) (change-state! &prevmsg $msgrcv) _)))
                             ($msg (get-state &prevmsg))
                             ($_ (if (and (> $k 1) $msgnew)
                                     (change-state! &loops (maxNewInputLoops)) _)))
                            (if (> (get-state &loops) 0)
                                (let* (($lastmessage (if $msgnew (HUMAN-MSG: $msg) (if (spamShield) " DO NOT RE-SEND OR SPAM!" "")))
                                       ($_ (change-state! &nextWakeAt (+ (get_time) (wakeupInterval))))
                                       ($_ (log INFO "loop" $lastmessage))
                                       ($send (py-str ($prompt :-:-:-: $lastmessage)))
                                       ($_ (log INFO "loop" (CHARS_SENT: (string_length $send) $send)))
                                       ($respi (llmProviderChat $send (maxOutputToken) (reasoningMode)))
                                       ($resp (py-call (action_protocol.parse_and_render_metta $respi)))
                                       ($response (if (== "(" (first_char $resp)) $resp (progn (log INFO "loop" $resp) (repr (REMEMBER:REPLY_ONLY_WITH_JSON: (actions ((tool send) (args ((text ...))))))))))
                                       ($sexpr (catch (sread $response)))
                                       ($_ (change-state! &error ()))
                                       ($_ (HandleError parse_error $response $sexpr))
                                       ($_ (log INFO "loop" (RESPONSE: $sexpr)))
                                       ($results (RESULTS: (collapse (let $s (superpose $sexpr) (COMMAND_RETURN: ($s (HandleError tool_runtime_error $s (catch (let $R (eval $s) (py-call (helper.normalize_string $R)))))))))))
                                       ($_ (log INFO "loop" $results))
                                       ($_ (py-call (tracing.end_iteration (repr $results)))))
                                      (progn (if (or $msgnew  (not (== $sexpr ()))) (addToHistory $msg $response $sexpr $msgnew) _)
                                             (change-state! &lastresults (string-safe (repr $results)))))
                                (if (> (get_time) (get-state &nextWakeAt))
                                    (change-state! &loops (+ 1 (maxWakeLoops))) _)))
                      (sleep (sleepInterval))
                      (cut)
                      (gc)
                      (omegaclaw (+ 1 $k))))))
