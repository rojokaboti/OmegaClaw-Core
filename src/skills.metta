(= (getSkills)
   (;INTERNAL:
    "- Remember a particular string such as skills and memories: remember string"
    "- Query long-term embedding memory for skills and memories with short phrases only: query string"
    "- Remember a provenance-tagged claim (recorded as your own llm-sourced belief, confidence 0.55): remember-claim string"
    "- Query memory and get results with source/confidence provenance annotations: query-claims string"
    "- Episodes searches history for episodes around a time stamp, time format is same as in TIME: episodes time_string"
    "- Pin a certain string as short-term working memory item to keep track of task state: pin string"
    ;SHELL AND FILE I/O:
    "- Execute shell command without apostrophe in string, it returns the command output to you: shell string"
    "- Read file to string: read-file filename"
    "- Write string to file: write-file filename string"
    "- Append line to file: append-file filename string",
    ;COMMUNICATION CHANNELS:
    "- Send message to user: send string"
    "- Search the web: search string"
    "- Search the web using the Tavily Search Agent: tavily-search string"
    "- Get technical analysis for a stock ticker using the Technical Analysis Agent: technical-analysis ticker"
    ;FILESYSTEM SKILLS (see LOADED_SKILLS catalogue for available names):
    "- Read a loaded filesystem skill's full instructions before following it: use-skill name"
    ;PLUGIN TOOLS (see PLUGIN_TOOLS catalogue for available names; pass \"\" arg if none needed):
    "- Call a plugin/MCP tool by name with one string argument: plugin-invoke name arg"
    ;SKILL WORKSHOP (capture reusable workflows for review; never changes active skills directly):
    "- After a repeated or tricky workflow worth reusing, draft it as a SKILL.md proposal for operator review: propose-skill name skillmd"
    ;CODE EXECUTION:
    "- Execute MeTTa expression: metta sexpression"
    "Example to invoke Non-Axiomatic Logic via MeTTa: "
    "metta (|- ((--> (× sam garfield) friend) (stv 1.0 0.9))"
    "          ((--> garfield animal) (stv 1.0 0.9)))"
    "metta (|- ((==> (--> (× $1 elephant) eat) (--> $1 ([] dangerous))) (stv 1.0 0.9))"
    "          ((--> (× tiger elephant) eat) (stv 1.0 0.9)))"
    "Also: note the $1 for independent variables, and for negated knowledge use (stv 0.0 0.9)"
    "Additionally |- also works for revision, to merge evidence even when the term of both premises is the same."
    "You can also use PLN:"
    "metta (|~ ((Implication (Inheritance $1 (IntSet Feathered))"
    "           (Inheritance $1 Bird)) (stv 1.0 0.9))"
    "          ((Inheritance Pingu (IntSet Feathered)) (stv 1.0 0.9)))"
    ;REASONING SESSIONS (persist premises across turns; isolated per session id):
    "- Create a named reasoning session: metta-session-create session_id"
    "- Add a premise (a sentence like ((--> a b) (stv 1.0 0.9))) to a session: metta-session-add session_id expr"
    "- Infer: combine a query premise with every stored session premise via |-: metta-session-infer session_id expr"
    "- Clear a session's stored premises: metta-session-clear session_id"
    "- Snapshot a session's state to disk for reproducibility: metta-session-snapshot session_id"))


(= (read-file $file)
   (progn (translatePredicate (exists_file $file))
          (translatePredicate (read_file_to_string $file $content ()))
          $content))

(= (write-file $file $str)
   (progn (translatePredicate (open $file write $Out))
          (translatePredicate (write $Out $str))
          (translatePredicate (close $Out))
          WRITE-FILE-SUCCESS))

(= (append-file $file $str)
   (progn (translatePredicate (exists_file $file))
          (translatePredicate (open $file append $Out))
          (translatePredicate (write $Out $str))
          (translatePredicate (nl $Out)),
          (translatePredicate (close $Out))
          APPEND-FILE-SUCCESS))

(= (tavily-search $query)
   (py-call (agentverse.tavily_search $query)))

(= (technical-analysis $ticker)
   (py-call (agentverse.technical_analysis $ticker)))

!(import_prolog_functions_from_file (library OmegaClaw-Core ./src/skills.pl) (shell first_char gc read_file_tail))

(= (metta $str)
   (let $code (sread $str)
        (repr (swrite (eval $code)))))

(= (pin $x)
   PIN-SUCCESS)

; Filesystem SKILL.md progressive disclosure (Issue #11). The compact LOADED_SKILLS
; catalogue (injected in getContext) advertises names+descriptions; this returns the
; chosen skill's full instructions on demand, with {baseDir}/{skillDir} resolved.
(= (use-skill $name)
   (py-call (skill_loader.get_skill_body $name)))

; Generic plugin/MCP tool dispatch (Issue #15). The PLUGIN_TOOLS catalogue (injected in
; getContext) advertises available tool names; this routes a call to the registered handler.
(= (plugin-invoke $name $arg)
   (py-call (plugin_registry.invoke $name $arg)))

; Governed skill workshop (Issue #14). Drafts a SKILL.md proposal into the review queue; it
; NEVER modifies active skills — an operator applies via the workshop CLI.
(= (propose-skill $name $body)
   (py-call (skill_workshop.propose_tool $name $body)))
