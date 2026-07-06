; FreeCiv strategy rules for the PLN-vs-plain-LLM A/B experiment.
;
; Each rule is a PLN **Implication** premise "situation => recommended action". Paired with an
; observed fact sentence via (|- fact rule), lib_pln's Modus Ponens clause
;   (|~pln ($A $T1) ((Implication $A $B) $T2)) -> ($B (Truth__ModusPonens $T1 $T2))
; derives the grounded recommendation: the rule's variable unifies with the fact's entity, so
;   ((Inheritance City_1 Undefended) (stv 1.0 0.99))  +  the Undefended rule
;     |-  ((Recommend City_1 Defend) (stv ...))
;
; This is genuine (if one-hop) symbolic inference through OmegaClaw's real MeTTa/PLN engine — not
; string formatting. Truth values are heuristic: high-ish frequency, moderate confidence.
;
; The fact vocabulary matches what benchmarks/freeciv/adapter.py emits
; (Inheritance <city> Undefended | LowFood ; Inheritance <unit> Type_settlers ;
;  Evaluation (Predicate Threatens) (List <enemy> <target>)).

(= (freeciv-rules)
   (superpose (
     ((Implication (Inheritance $c Undefended) (Recommend $c Defend)) (stv 0.9 0.8))
     ((Implication (Inheritance $c LowFood) (Recommend $c Food)) (stv 0.9 0.8))
     ((Implication (Inheritance $u Type_settlers) (Recommend $u Settle)) (stv 0.8 0.7))
     ((Implication (Evaluation (Predicate Threatens) (List $e $t)) (Recommend $t Retreat)) (stv 0.85 0.75))
   )))

; Pair one observed fact sentence with every rule and return the derived recommendations
; (nondeterministic). A non-matching (fact, rule) pair yields nothing.
(= (recommend-for $fact)
   (let $r (freeciv-rules) (|- $fact $r)))
