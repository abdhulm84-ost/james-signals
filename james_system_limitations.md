# James pipeline — what we can't handle, why, and how to resolve

Running list during message review. Tag each as **SYSTEM** (our code/model) or
**BROKER** (venue/API/permission). Consolidated resolutions at the end.

## Format per entry
```
CASE:      <the message/case>
LIMIT:     SYSTEM | BROKER
WHY:       <root cause>
RESOLVE:   <how to fix — and effort>
```

---

## Running entries (as messages are read)

### L1 — map levels captured but never used
```
CASE:   NDX map (S1-R3, bullish/bearish triggers) — the master plan message
LIMIT:  SYSTEM
WHY:    map_record() stores the levels as a ledger row, but nothing scores later
        trades against the zones. Yesterday proved zone discipline predicts
        quality (trades at his S/R held up; sells 30 pts before his own R1 were
        thin). We capture the map and then ignore it.
RESOLVE: book-side correlation step — when an entry lands, compare its level to
        the stored map zones and flag "at zone / early / chasing". Small.
```

### L2 — map template not extracted
```
CASE:   same map, 2 days running
LIMIT:  SYSTEM
WHY:    levels are a FIXED TEMPLATE around spot (spot±150 = S1/R1, spot±80 =
        trigger band), translated each day. We store 6 flat numbers and lose
        the template + the posted-spot drift (map said 29930, live 29597 = -333).
RESOLVE: capture spot + band + posted_spot; derive/verify levels instead of
        trusting them flat. Small.
```

### L3 — compound message = 3 classes in one
```
CASE:   "Sell SPX 7735C n Buy 7745C collect $550 / We are holding Bear calls from
        yesterday. we will see a big drop / Today is FOMC Day ... SPX between
        7760 and 7660"
LIMIT:  SYSTEM
WHY:    ONE message carries THREE classes: (1) vertical entry, (2) hold+prediction,
        (3) SPX range map + FOMC context. Parser emits ONE card per message, so
        it must pick one and drops the rest. We lose the range and the hold.
RESOLVE: multi-record output (spec §1 already says "two rows, not one") — split a
        message into per-class records before dispatch. Medium.
```

### L4 — screenshot fields not readable
```
CASE:   same message + screenshot (position screen, presumably the held bear calls)
LIMIT:  SYSTEM (and model-vision)
WHY:    I cannot see the image (no vision in this model). The screenshot holds
        qty / expiry / avg credit / account — the fields text never gives.
RESOLVE: image-extraction pass (spec A4) returning qty/expiry/fill/account into
        the record. Medium-high (needs a vision-capable parse step).
```

### L5 — event context (FOMC) not captured
```
CASE:   "Today is FOMC Day ... many opportunities on both sides"
LIMIT:  SYSTEM
WHY:    FOMC is a RISK MODIFIER (wider range, both-side whipsaw, size reduction),
        not a trade. We record neither the event nor its effect on sizing/stops.
        Yesterday's "FOMC tomorrow, choppy" was likewise dropped — and the
        overnight holds into it were the losing legs.
RESOLVE: context field -> event flag that widens stop / cuts size / defers
        overnight carries. Small-moderate.
```

### L6 — range in text lost to the entry
```
CASE:   "SPX will trade mainly between 7760 and 7660 today"
LIMIT:  SYSTEM (same root as L3)
WHY:    SPX-native range stated in text, but the message is dominated by the
        entry so it becomes one entry card. The range is the PLAN the entry
        should be scored against (entry 7735 = 25 below top, 75 above bottom).
RESOLVE: per-class split (L3) + range captured as a map record. Small once L3 done.
```

### C1-REVISED — screenshot % is the metric, not a recompute
```
CASE:   "15%" (text) vs screenshot showing 14.61%
LIMIT:  SYSTEM (my analysis was wrong; the spec rule C1 is wrong)
WHY:    I recomputed "actual %" as PREMIUM DECAY ((credit-now)/credit) and called
        the gap a ~10pt optimism bias. But the screenshot reports a DIFFERENT
        metric (position P&L% on some denominator) at 14.61% while text rounds
        it to 15%. So text% ~= screenshot% (a rounding), and my decay% was an
        unrelated number. The "bias" was a metric mismatch, not optimism.
RESOLVE: (a) capture screenshot % VERBATIM as the source of truth (14.61, not 15);
        (b) identify the denominator (margin? credit? max loss?) before any
        recompute; (c) store premium/price separately so decay is a distinct,
        labelled metric — never overwrite the reported %. REVISES spec C1.
```

### L11 — percentage precision lives only in the image
```
CASE:   "15%" in text, 14.61% in screenshot
LIMIT:  SYSTEM (and model-vision)
WHY:    text rounds; the precise figure is image-only. A 0.39pt gap here is small,
        but the same rounding could hide real differences on bigger numbers.
RESOLVE: image extraction returns the exact pct (L4/A4). Until then, treat text
        % as approximate and never as a precise field.
```

### C1-RESOLVED — the % denominator is RISK (max loss / margin)
```
CASE:   "30%+ on 7735/7745, buy back at $420" — exit of the spread sold at $550
LIMIT:  none (this SOLVES the open question)
WHY:    entry 5.50 -> exit 4.20 = +$130/contract.
        as % of credit = 23.6%   |   as % of max loss (450) = 28.9% ~= "30%+"
        => the reported % is P&L / RISK (max loss, i.e. margin), NOT return on
        credit. This is the denominator the screenshots report (14.61% earlier
        was the same metric at an earlier mark).
RESOLVE: store pct with denominator=RISK; compute R = P&L / max_loss. Note this
        is the OPPOSITE of what I assumed (I was using return-on-credit).
```

### L12 — exit price can be stated in TEXT (rare but valuable)
```
CASE:   "Buy back at $420" — the exit price, in the message body
LIMIT:  SYSTEM (we assume exit prices are image-only)
WHY:    our rule "exit price lives only in screenshots" is wrong for this case.
        "buy back at $X" / "collect $X" gives the fill in text. This is the
        FIRST exit with a computable P&L from text alone (5.50 -> 4.20).
RESOLVE: parse "buy back at / sold at / collect $N" as the exit price field.
        Then P&L = (entry_credit - exit_debit) * 100 * qty. Small.

### L13-RESOLVED — Tradier is the right venue for multi-leg (research done)
```
CASE:   James uses "2 accounts in RH and Tradier" — is Tradier API-viable?
LIMIT:  none — RESOLVED by research (agent-reach, Tradier docs)
WHY:    Tradier is the strongest fit we've found:
  - place_multileg_option_order: 2-4 LEGS NATIVE (verticals, iron condors, iron
    flies) — no legging in, no shared-strike netting problem
  - FREE API access for Tradier Brokerage account holders
  - FREE SANDBOX ("build and test with no money at risk"), 15-min delayed data
  - NEW ZEALAND explicitly permitted (Australia blocked)
  - OCC symbols; equity + option + multileg + combo; OCO/OTO/OTOCO
  - Level 1 data only; US equities+options only; rate limits "loose"
  - personal-use only unless a Tradier Partner
WHY IT MATTERS: it is ALSO the broker James actually uses, so his multi-leg
  structures map 1:1 — and the shared-strike ladder (L14) that nets on one
  account can be placed as intended across RH + Tradier.
RESOLVE: build the multi-leg executor on Tradier (sandbox first). This becomes
  the venue for B1/B2. Effort: medium (clean REST, well-documented).
  ALTERNATIVES CONSIDERED: Tiger (MLEG exists but clunkier; we already have it),
  Tastytrade (options-native but newer API), Schwab (API access gated), IBKR
  (NZ CFD restriction is irrelevant for options but heavier integration).
  Tradier wins on: native multileg + free + sandbox + NZ + matches James's setup.

### L17 — Tradier delayed-data concern (RESOLVED: it's sandbox-only)
```
CASE:   "Tradier sandbox delays data 15 min" — how to avoid?
LIMIT:  none — RESOLVED (Tradier docs, Market Data page)
WHY:    the 15-min delay is SANDBOX ONLY. Brokerage API (production) delivers
        REAL-TIME equities AND options, FREE to account holders. Sandbox is
        delayed for structure testing only.
        Asterisks: NDX/RUT/COMP are derived (may lag); non-CBOE-main indexes
        15-min; Greeks hourly. SPX / QQQ / individual stocks = real-time.
RESOLVE: develop + regression in sandbox (15-min ok for structure), trade in
        production for real-time. This removes the need for the independent
        options-quote workaround planned for Tiger. Effort: none (config switch).
```

### L18 — time-based exit / deadline (NEW mechanism)
```
CASE:   "Until 1pm CST choppy ... do an Iron fly ... close it before FOMC"
LIMIT:  SYSTEM
WHY:    the exit is a DEADLINE (before the FOMC print at 1pm CST = 2pm ET), not a
        price level. Our exits are price-based (stop/target). A time-boxed scalp
        that holds past the deadline loses its whole edge — here the 95%-credit
        fly goes from a pin bet to a volatility bet at the announcement.
RESOLVE: add an exit_by field (time/deadline or event) alongside stop/target;
        manage loop must force-close at the deadline regardless of price. Also
        events (FOMC) should auto-arm a deadline. Moderate.
```

### L19 — wing_width vs spread width (my own error, worth guarding)
```
CASE:   iron fly body 7735, wings 7730/7740 — I first used width 10 (wing-to-wing)
LIMIT:  SYSTEM (parser must not repeat this)
WHY:    for an IRON FLY the risk width is BODY-TO-WING (5), not wing-to-wing (10).
        Using 10 gave max loss $525 / 0.9:1 / 48% prob — wrong. Correct is
        max loss $25 / 19:1 / 95% prob (matches his stated $25 risk exactly).
        max_loss = (body_wing - credit)*100 ; prob = credit/body_wing.
RESOLVE: store body_wing explicitly (not just min/max strikes); use it for fly
        risk math. Do NOT use (wing_high - wing_low) for flies.

### L21 — "Nth target done" is an ambiguous target-ladder exit
```
CASE:   "1st target done✅" (separate message, no instrument/value/qty)
LIMIT:  SYSTEM
WHY:    implies a target LADDER (e.g. GLW 3.9/5.1/6.6) where "1st target done" =
        a partial exit at targets[0]. But: WHICH position (5+ open across names)?
        WHICH target value? HOW MUCH closed (1/3? 1/2?)? None stated. Same class
        as the decay markers: exit/metric with no position reference.
RESOLVE: (a) treat targets[] as a LADDER of trim levels (target[0] -> trim x%);
        (b) "Nth target done" -> partial close at targets[N-1] with a defined
        default fraction; (c) if multiple positions are open, ALERT instead of
        guessing the match. Moderate.
```

### L22 — same-expiry 5-wide battery = portfolio, not one-offs
```
CASE:   MCHP/QCOM/SMCI/TXN all 5-wide bear calls, all exp 11/6, different names
LIMIT:  SYSTEM (book model)
WHY:    he runs a DIVERSIFIED battery of same-expiry 5-wide bear calls. We model
        each as an independent trade. Portfolio view (total risk across names,
        correlation, same-expiry concentration) is never computed — yet that is
        where the real risk lives (all expire together on 11/6).
RESOLVE: book-level rollup: total credit, total max-loss, expiry concentration,
        and a cap on same-expiry aggregate risk. Moderate.

### L21 -> FOLDED INTO SPEC §4.7 (position matching)
```
RESOLUTION captured in james_parser_spec.md §4.7:
  targets[] = ladder of trim levels; "Nth target done" = partial close at
  targets[N-1]. Matching order: (1) image identity [needs L4], (2) price-match
  (targets[N-1] breached by live price = the match), (3) recency, (4) AMBIGUOUS
  -> ALERT, never trade. Trim fraction default = 1/len(targets) (equal ladder),
  overridable by a stated %. Guiding principle added to spec: a wrong trade is
  worse than a missed one.

### L23 — resting limit EXIT order (new mechanism)
```
CASE:   "We can keep a Limit order to buy back at 4.4 n close it in few minutes"
LIMIT:  SYSTEM
WHY:    this is a RESTING LIMIT to close (GTC buy-to-close at a price), not a
        market-on-trigger exit. Our exits fire a market/limit order when stop or
        target is breached. Here the exit is a standing order that fills when the
        price is touched — set-and-forget. Different mechanism (cf L18 time-exit).
RESOLVE: exit_order field = {type: limit|market|time|event, price, duration}.
        manage loop must support resting close orders. Moderate.
```

### L24 — split-leg exit (close one leg, hold another)
```
CASE:   "book gains on Bull Put Leg 7730/7735 first and then hold 7735/7740 for FOMC"
LIMIT:  SYSTEM
WHY:    this is a LEG-SELECTIVE exit: close the 7730/7735 leg, KEEP the 7735/7740
        leg running (with conviction "make more gains"). It is NOT a full close
        and NOT a uniform trim — it picks which leg to close. Our exit closes a
        whole position or a fraction of the whole, never a chosen leg.
RESOLVE: exit must address LEGS, not just positions: close_leg(strike_pair),
        hold_leg(strike_pair). Ties into the spread book (B2) and the ladder
        model (L14). Moderate-high.
```

### L25 — width varies by instrument (refines the wing-width finding)
```
CASE:   QQQ 720/722 = 2-WIDE, vs 5-wide on SPX/SMCI/TXN/MCHP/QCOM
LIMIT:  none (a refinement, not a gap)
WHY:    the "5-wide" default holds for index + higher-priced names; lower-priced
        underlyings (QQQ ~725) use a NARROWER 2-wide. So width is
        instrument-relative, not fixed. QQQ 2-wide gives R:R 0.74:1 (FAVOURABLE),
        unlike the 5-wide which ran ~1.2:1 against.
RESOLVE: width must be READ per message, never assumed; store body_wing (L19).
        Note favourable R:R on tight widths = the better setups.
```

### L26 — exit-price reference ambiguous across a compound message
```
CASE:   "buy back at 4.4" but the QQQ credit is only 1.15
LIMIT:  SYSTEM (and information-theoretic)
WHY:    4.4 does not fit the QQQ vertical as a gain (1.15 credit). It likely
        refers to the BULL PUT leg (higher premium) or a screenshot value. The
        message mixes a QQQ entry + an SPX bull-put plan + an exit price, so the
        exit price cannot be bound to a position from text alone.
RESOLVE: bind exit prices to the position they name; if unstated, use the §4.7
        matching (price + recency) and ALERT if ambiguous. Same principle:
        never guess.

### L27 — explicit close ORDER in text (resolves L24/L26 when present)
```
CASE:   "you can do Sell to Close 7730P n Buy to close 7735P in your broker"
LIMIT:  none — this is the GOOD case; it sets the standard
WHY:    unlike "book gains" (vague) this names the legs AND the closing side:
        7730P long -> Sell to Close; 7735P short -> Buy to Close. Nothing to
        infer. It is the operationally-complete exit form.
RESOLVE: when explicit close instructions are present, execute them VERBATIM and
        skip inference. Fall back to §4.7 matching only when they are absent.
        Parser should extract: close_legs[{strike, right, action: buy_to_close|
        sell_to_close}]. This is the highest-fidelity exit and should be preferred.
        Note the rule: a bull-put spread closes as "Sell to Close <long strike>P
        + Buy to Close <short strike>P" (reverse of entry).

### L28 — IRON FLY = 2 independently-managed verticals (MAJOR, reframes B1/B2/L24)
```
CASE:   "Iron fly is combo of 2 Legs (Bull Put and Bear call), we took gains on
        Bull Put leg now and in few minutes, will take on bear call"
LIMIT:  SYSTEM (model was wrong; this is the correct model)
WHY:    an iron fly is NOT one atomic 4-leg order. It is TWO VERTICALS sharing a
        body:
          fly(body) = Bear call (sell body C, buy higher C)   <- close at SUPPORT
                    + Bull put  (sell body P, buy lower P)    <- close at RESISTANCE
        each closed INDEPENDENTLY at the price extreme that favours it. This is
        why he splits across RH + Tradier (L14): the shared body would net on
        one account. And it explains L24: the "split-leg exit" is really closing
        one whole component vertical while holding the other.
RESOLVE: model a fly as 2 component verticals (bear_call + bull_put) with a shared
        body, each independently closeable. Exit rule: price at RESISTANCE ->
        close bull-put side; price at SUPPORT -> close bear-call side. This
        REPLACES the "atomic 4-leg" assumption in B1 and resolves L24 at the root.
        Effort: high (book model), but it is the correct architecture.

### L29 — RUT (Russell 2000) = new instrument class + venue split
```
CASE:   "Sell RUT 2790C n Buy 2795C collect $415 ... RUT at top in 1H, drop 40-50 pts"
LIMIT:  SYSTEM + BROKER
WHY:    RUT is the third index (SPX/NDX/RUT) and maps to THREE instrument classes:
          RUT  = Russell 2000 INDEX OPTIONS (this msg) -> Tradier (Tiger rejects NDX/RUT class)
          RTY  = Russell 2000 FUTURES ("bull trap on ES,NQ,RTY")
          US2000 = Russell 2000 CFD (BlackBull has this)
        BlackBull only has the CFD; the index options need Tradier. Alias the three
        to one underlying identity but keep the instrument_class distinct.
RESOLVE: underlying_class field (index_option | future | cfd | equity_option);
        route by class (options -> Tradier, CFD -> BlackBull). Alias RUT/RTY/US2000
        -> RUSSELL2000 identity. Moderate.

### L30 — "lotto" class + RISK TIER field (new class)
```
CASE:   "GLD 370P at 1.62 for today - Lotto, high Risk, can make 100% or more or 0"
        "For high risk takers"
LIMIT:  SYSTEM
WHY:    a LOTTO is not a bracket: no stop, no targets, BINARY payoff (100%+ or 0),
        0DTE, premium = max loss. And the text carries explicit RISK-TIER labels
        ("Lotto, high Risk", "For high risk takers"). We have no risk_tier field,
        so a lotto would be sized/managed like a normal bracket.
RESOLVE: add class "lotto" + risk_tier field (normal | high_risk | lotto).
        Handling: lotto = capped small size, no hold, no laddering, exit at
        expiry or a stated target; NEVER inherit bracket defaults (no SL/target).
        Risk tier must gate SIZE (lotto -> smallest). Moderate.
```

### L31 — account-level P&L target (new field: pnl_goal)
```
CASE:   "Target 30k to 40k today"
LIMIT:  SYSTEM
WHY:    a DAILY P&L GOAL in dollars — account-level, not a trade or price level.
        Distinct from trade targets (SPX 7700) and price predictions ("drop 40
        pts"). We have no field for an account-level goal, so it is discarded as
        narrative. It IS calibratable: target $30-40k vs realised (prior day RH
        = $22,501).
RESOLVE: add a pnl_goal record (type: goal, amount, horizon: day) and track
        realised-vs-goal in the calibration. Also gates SIZING when a goal is
        ambitious (chasing a target -> the risky behaviour). Small.

---

## DESIGN EVOLUTION (what the message-by-message review has changed)

The review is not just finding gaps — it is rewriting the model. Recorded so the
end-of-pass build targets the FINAL model, not the original one.

| Aspect | Original design | After review |
|---|---|---|
| Unit of work | message = trade | message = typed RECORD; trade = managed STRUCTURE |
| Iron fly | 1 atomic 4-leg order | 2 verticals sharing a body, closed independently (L28) |
| Exit grammar | stop/target | + book gains, % on, buy-back-at-$X, Nth-target-done, resting-limit-exit, split-leg-exit, time-exit, explicit-close (7+ forms) |
| Risk control | none | gate by RISK TYPE (defined vs not); size by credit/width; phased lotto |
| Quality metric | none | credit/width predicts R:R (monotonic) |
| Venue | Tiger only | Tradier (multi-leg+real-time) + BlackBull (CFD) + Tiger (legacy) |
| Accounts | single book | multi-book (RH / Tradier / funded) — netting matters (L14) |
| Prediction | ignore | record + calibrate (level/path strong, "100%" weak) |
| Sizing | fixed notional | by risk tier + quality + account |

## OPEN DESIGN QUESTION — WHERE DOES RISK CONTROL SIT?

To resolve at the end (per user: "see how the risk control shud be placed"):
risk control could live at (a) PARSE time (reject records), (b) EXECUTE time
(gate each trade), (c) PORTFOLIO level (cap aggregate, same-expiry
concentration, pnl_goal chasing). Likely DEFENSE IN DEPTH — all three, with the
hard gate at execute and the caps at portfolio. Decide the placement and the
responsibility split before coding.

### L28-ADDENDUM — the two verticals have INDEPENDENT lifecycles
```
CASE:   "earlier we took gains with Bull Puts when SPX moved up, now we take with
        Bear call leg"
LIMIT:  none — confirms L28, adds a refinement
WHY:    the fly's two verticals share a body but have SEPARATE entries and
        separate exits (bull put closed on the up-move; bear call closing on the
        down-move). They are two independently-timed trades, not one position
        with one lifecycle. Total fly P&L = sum of two separately-closed legs.
RESOLVE: the book must track each component vertical as its own position-in-time
        (own entry_ts, exit_ts, P&L), linked only by the shared body. Do NOT
        collapse a fly into a single lifecycle record. Refines L28 + L22.
        CONFIRMS the range-harvest rule (close each side at its favourable
        extreme) — both sides were collected here.

### L15-ADDENDUM — exits can target a GROUP by scope (not just one position)
```
CASE:   "Trim 80% on today's bear calls" + "25% to 30% on all bear calls one more time"
LIMIT:  SYSTEM
WHY:    the exit targets a SET defined by a QUERY: (opened today) AND
        (structure = bear call) -> trim 80% of ALL of them. Not one named
        position, not a matched one — a SCOPE. Also a REPEAT trim ("one more
        time"): he scales out as the move runs rather than one full exit.
RESOLVE: exit addressing must support scope = {time_window, structure_type,
        direction, underlying_class} in addition to a named position. Scoped
        trims need an explicit trim% (80 here — when he means trim he states
        the number). Group-execution must confirm the resolved set before acting.
        Note: scope trims are the case where an AMBIGUOUS match is actually fine
        (the scope IS the specification), unlike "Nth target done".

### L32 — scope can be a SECTOR/category (refines L15)
```
CASE:   "All Chip Stock bear calls in nice gains but we can hold for more"
LIMIT:  SYSTEM
WHY:    the group scope here is a CATEGORY ("Chip Stock" = MCHP/QCOM/SMCI/TXN),
        not just time+structure. So scope dimensions include a sector/tag.
RESOLVE: scope = {time_window, structure_type, direction, underlying_class,
        SECTOR/category}. Needs a sector tag on each underlying (semis, index,
        commodity...). Refines L15.
```

### L33 — active HOLD decision ("hold for more") + exit MECHANISM reason
```
CASE:   "we can hold for more" + "Booking gains ... IV and theta decay helping"
LIMIT:  SYSTEM
WHY:    (a) "hold for more" is an active decision to NOT exit winners — distinct
        from a passive status ("we are holding"). The book must honour a hold
        (do not auto-close on a target if he says hold for more).
        (b) "IV and theta decay helping" is the MECHANISM of the exit (vol+time),
        not a price trigger. Exits can be mechanism-driven, not just level/time.
RESOLVE: (a) hold directive field (hold_until / hold_for_more) overrides auto-exit;
        (b) exit_reason captures mechanism (iv/theta/price/time/event). Moderate.
```

### CALIBRATION — first LOTTO result
```
CASE:   "GLD Puts in gains, 25% up, Take gains" (the GLD 370P lotto)
RESULT: managed OUT at +25% (~\$41/contract on 1.62 entry) — NOT the "100% or 0"
        binary. So lottos can be TRIMMED, not only held to expiry.
IMPLIES: the lotto risk model ("0 or 100%") is itself wrong — they are managed
        positions with partial exits. Feeds the Phase-2 lotto decision.

### L34 — trade GRADE field ("A+ for Funded")
```
CASE:   "QQQ Selling 724C for today n collecting $135 - A+ for Funded"
LIMIT:  SYSTEM
WHY:    "A+" is a QUALITY/CONVICTION GRADE, and "for Funded" is the account tag.
        We model confidence as 0-100 but not a letter grade. The grade is a
        second conviction signal (besides confidence) and may map to sizing.
RESOLVE: add grade field (A+/A/B... ) alongside confidence; map to a size tier.
        Capture account tag with it. Small.
```

### L35 — conditional exit ("when you see them in green")
```
CASE:   "Pl book gains on 7720/7730 n 7725/7735 Bear calls when you see them in green"
LIMIT:  SYSTEM
WHY:    the exit is CONDITIONAL on profitability — not immediate, not a price
        level, but a state ("when in green"). Our exits fire on stop/target/time;
        this fires on the position reaching a profitable state.
RESOLVE: exit trigger type "in_profit" / "when_green" alongside price/time/event.
        The manage loop must check unrealised P&L sign, not just levels. Moderate.
```

### L36 — explicit STOP-LOSS directive (cut a loser)
```
CASE:   "RUT is not moving much, take a small stop loss with it"
LIMIT:  SYSTEM
WHY:    an explicit LOSS-TAKING decision on a named position (RUT), with a size
        hint ("small"). Everything until now was gains; this is a CUT-THE-LOSER.
        Our stops are pre-set at entry; this is a discretionary stop called live.
RESOLVE: exit_reason = stop_loss (discretionary), with a size qualifier. Must
        honour a live stop-loss call even if the pre-set stop hasn't hit. Moderate.
```

### L37 — expiry-scoped split exit (6th scope dimension)
```
CASE:   "Holding tomorrow's bear calls, took gains on today's"
LIMIT:  SYSTEM
WHY:    the exit splits by EXPIRY: close the 0DTE (today, max decay), hold the
        longer-dated (tomorrow). Scope dimensions now: time-window, structure,
        direction, underlying_class, sector, EXPIRY. Our exit scoping has none
        of these granularities.
RESOLVE: scope.expiry filter (today | tomorrow | date); split-decisions must
        resolve per expiry bucket and act on each. Refines L15/L32.
        NOTE: "hold tomorrow's" = an overnight carry — the exact pattern that lost
        on every prior overnight (calibration). Flag overnight holds as the
        highest-risk action per the risk policy.

### L38 — derive wing width from the risk/reward line (spec 4.5, run in reverse)
```
CASE:   "Iron fly for AMZn at 255 and collect $462, $38 risk and $462 reward"
LIMIT:  none — a PARSER CAPABILITY (extends spec 4.5)
WHY:    the message gives body (255), credit (4.62), risk ($38), reward ($462)
        but NEVER the wings. Yet width = risk/100 + credit = 0.38 + 4.62 = 5.00
        -> wings 250/260. The risk/reward line RECONSTRUCTS the structure.
        (5th exact risk/reward match.)
RESOLVE: run spec 4.5 in REVERSE: when wings are unstated, derive
        width = risk/100 + credit, then body +/- width = wings. This also gives
        a consistency check: if derived width != stated legs, the parse is wrong.
        Enables prob (credit/width) and R:R on "incomplete" messages. High value.

### L39 — ACCOUNT STATE (buying power + expiring positions) is untracked & image-only
```
CASE:   screenshot: "buying power: $65,756.98" + "you have SPXW options expiring this week"
LIMIT:  SYSTEM (and image-only)
WHY:    two account-level facts we never capture:
        (1) BUYING POWER — the hard constraint on position size. Reveals account
            scale (~$65k margin available; consistent with $32k RH + Tradier +
            funded books). Our fixed $1,000 notional is disconnected from both.
        (2) EXPIRING POSITIONS — a broker reminder that reveals open positions and
            their expiry. Ties to L22 (same-expiry concentration): expiry is where
            defined-risk spreads resolve.
RESOLVE: track account_state {buying_power, equity, expiring[]}; size against
        buying power, not a fixed notional; surface expiring positions in the
        daily digest. Also: these are IMAGE-ONLY — extraction (A4) required.
        Moderate-high value for risk management.

### L40 — self-reported accuracy + earnings-event causality
```
CASE:   "7% drop on GOOG as expected n explained in Live yesterday"
        "NDX, SPX should drop tomorrow due to GOOG and AMZN earnings"
LIMIT:  SYSTEM
WHY:    (a) SELF-REPORTED ACCURACY: he claims prior correct predictions ("as
        expected"). Without the earlier message we cannot verify — so his stated
        track record is uncheckable. Meta-calibration gap: track his CLAIMS
        against our logged predictions.
        (b) EARNINGS->MARKET CAUSALITY: macro calls hinge on GOOG/AMZN earnings
        dates. We track no earnings calendar, so these predictions are
        unverifiable and the event risk is invisible to the book.
RESOLVE: (a) prediction ledger already logs outcomes — ADD a claimed_hit flag and
        cross-check "as expected" against the record. (b) add an earnings/event
        calendar so event-driven predictions are checkable and event risk is
        surfaced. Moderate.

### L41 — theta-crush is the ENTRY THESIS for flies (extends L33 to entries)
```
CASE:   "SPX closed 7743.9 so over $200+ on each Iron fly ... AMZN choppy (Both
        Call Buyers and Put Buyers will be in big loss) ... if it stays around 255,
        our Short Iron fly will make 100%. Risk is very low with AMZN."
LIMIT:  SYSTEM
WHY:    the mechanism ("both call and put buyers in big loss" = long sides decay to
        0, short spreads collect) is the REASON TO ENTER an iron fly in chop, not
        just an exit reason. We capture exit mechanism (L33) but not entry thesis.
        Also: "Risk is very low" is his own risk read — a signal we ignore.
RESOLVE: entry_thesis field captures mechanism (chop/decay/iv/earnings) alongside
        direction; and capture his risk read ("very low") as a confidence signal.
        VERIFIED OUTCOME: 7740 fly = $210/contract from the SPX close 7743.9
        (matches "over $200"), independently computed. Moderate.

### L42 — "Ignore / avoid X" directive (NEGATIVE instruction, can RETRACT a prior call)
```
CASE:   "Ignore AMZN as ER is tomorrow but we can make nice gains tomorrow before
        earnings with AMZN"  +  "Near to 40k from 30k"
LIMIT:  SYSTEM
WHY:    (a) "Ignore X" is an explicit SKIP/AVOID — the opposite of enter/exit/hold.
        We have no negative instruction. AND it RETRACTS the prior call (the
        previous message said "Risk is very low with AMZN", predicted 100%);
        "ignore" overrides/cancels any pending trade on that name.
        (b) "make gains BEFORE earnings" = event-timed strategy (needs the L40
        earnings calendar to know "ER is tomorrow").
        (c) "Near to 40k from 30k" = P&L progress vs the L31 goal -> he HIT the
        30-40k target (calibration: goal forecast was accurate).
RESOLVE: add action "ignore"/"avoid" that (i) blocks trading the name until a new
        call, (ii) CANCELS any pending entry on it. Tie to the earnings calendar
        (event-risk window). Track pnl_progress vs pnl_goal. Moderate-high.

### L43 — SESSION-OPEN trigger (refines L18 time-exit) + cross-sector portfolio breadth
```
CASE:   "At around 7:15pm cst, we can make 50% or more on all Bear calls once RH
        Evening Session Opens" + "bear calls for SPX,QQQ,SLV,GLD,GOOG,QCOM,TXN,SMCI,MCHP"
LIMIT:  SYSTEM
WHY:    (a) the trigger is a SESSION BOUNDARY (RH extended-hours opening 7:15pm
        CST), not a clock time. Our time-exits use wall-clock/deadline; this
        fires on a VENUE SESSION OPEN. Also reveals RH evening session is
        tradable — our session model doesn't know it.
        (b) the book spans 9 underlyings / 4 sectors (index, metals, chips, tech),
        all bear calls, one direction, one catalyst -> cross-sector CORRELATED
        risk. L22 (portfolio) at its extreme: aggregate exposure is the real risk.
RESOLVE: (a) exit_by can be {session_open|session_close, venue} not just a time;
        model venue sessions incl. extended hours. (b) portfolio rollup must
        aggregate CORRELATED positions across sectors (same-direction
        concentration). Moderate-high.

### L44 — LIVE vs PAPER distinction (critical for interpreting results)
```
CASE:   "we made $1500 with ES and NQ Shorts in Live in less than 20 minutes"
LIMIT:  SYSTEM
WHY:    he trades REAL MONEY ("in Live"), not just paper. His 2k->43k P&L is
        likely REAL. Our pipeline is paper. So his results cannot be read as
        paper results, and his risk-taking reflects real stakes. Also means the
        account(s) mix live + paper (cf the RH/Tradier/funded books).
RESOLVE: add a venue_purpose field (live|paper) to records/accounts; never
        conflate his live P&L with our paper track. Moderate.
```

### L45 — multi-conditional PLAN (two trigger/action branches)
```
CASE:   "If SPX breaks 7708, 7690/7680 next targets. If it reaches 7660, we can
        make with Bull Puts again"
LIMIT:  SYSTEM
WHY:    a COMPOUND conditional plan: two IF/THEN branches in one message
        (break 7708 -> targets 7690/7680 ; reach 7660 -> bull puts). Our parser
        handles single conditionals; a multi-branch plan loses the second branch.
        Same root as the earlier "range 7670-7710 + bear calls/bull puts" message.
RESOLVE: plan record with branches[{condition, action, levels}]; store all
        branches, score later trades against each. Moderate.
```

### L46 — P&L GOAL/COMPOUNDING track is the driving narrative
```
CASE:   "40k target done from 2k, next step 50k" / "41.3K done" / "42.5k done"
        / "43k coming" / "One day good enough to cover your few years of fee"
LIMIT:  SYSTEM
WHY:    the account is COMPOUNDING (2k -> 43k) and the goal drives the trading
        (scaling up toward 50k). This is a running P&L PROGRESSION, not a trade.
        Ties to L31 (pnl_goal) + L39 (buying power). Risk: chasing a goal ->
        oversized risk (the exact Phase-1 concern).
RESOLVE: track pnl_progress as a running series (start, current, goal); flag
        when the goal is ambitious vs recent performance. Also feeds the risk
        policy (never chase a goal). Moderate.
```

## Resolved 2026-11-09 (image path)

```
CASE:   4047 five-leg image, 3955/3956/3963/3964/3993 — all "Undefined symbol"
LIMIT:  SYSTEM
WHY:    vision read chart labels and ticket quantities as strikes/roots:
        "PMH" (a pre-market-high tag) became the OCC root; "SELL TO OPEN 25
        SPXW 7630.000 C" put the QUANTITY in the strike slot, producing a
        4-leg structure with two legs at 25. Tradier rejected every one.
RESOLVE: DONE — root gate (unknown root + no quote parks), ticket qty strip,
        thousands separators kept, off-tick strike guard parks charts, >4 legs
        park instead of being silently truncated by legs[:4]. Parked images now
        keep their LEVELS (range high/low, buy/sell tags) in the ledger rather
        than discarding them.
```

```
CASE:   4033 bare "buy IONQ 29C at 0.8" — logged with an empty symbol
LIMIT:  SYSTEM
WHY:    the brain returned neither an option object nor a ticker, so the card
        fell to the equity path; and the far-strike guard fired on long singles,
        where distance to strike does not change the risk (premium is the risk).
RESOLVE: DONE — single contract rebuilt from the text before the no-trade gate;
        far-strike guard now applies to short premium only. Route pinned by
        test_route_4033.py (drives process_message with a useless brain card).
```

```
CASE:   3734 (Sell SPX 7615P / Buy 7610P, "for today") and 3735 vanished
LIMIT:  SYSTEM
WHY:    the listener dropped both while the pipe restarted. The 10-min gap
        reconciler existed but was invoked through asyncio.to_thread on an ASYNC
        function — to_thread returned an un-awaited coroutine, so the check had
        never executed once in its life (zero gap-check lines in the whole log).
RESOLVE: DONE — _guarded awaits coroutine functions; the body is module-level
        gap_backfill() and is pinned by test_gap_backfill.py. Cost of the miss:
        SPX closed 7541.4 against a 7615 short put, so 3734 was a max loss
        (-$270/contract, -$540 at the stated double size) — the miss saved money.
```

```
CASE:   Saved Messages turned into a firehose (1,723 pushes; 812 warnings, 572 of
        them ONE BMRN close re-pushing every cycle) — and a deaf listener was
        invisible
LIMIT:  SYSTEM
WHY:    push() had no repeat memory and every record-only notice pinged; the
        listener could go deaf while the loop ticked, with only retry noise in
        the log (3734/3735, 3854-3857).
RESOLVE: DONE — identical events collapse for 30 min keyed on the trade headline
        (a changed HTTP code does not re-ping); EXIT NOT HELD, unmatched EXIT
        TARGET, OPTION IDEA and IMAGE CONTENT are ledger/digest only
        (`push_quiet_off` reverts). New 60s listener watchdog: newest-id probe,
        immediate gap recovery, client re-arm after 2 silent cycles or 3 probe
        failures; heartbeat carries pushed/quiet/silent_streak. Pinned by
        test_push_guard.py.
```

```
CASE:   vision layer had no harness; chart levels captured but unusable; an
        unknown root could only park
LIMIT:  SYSTEM
RESOLVE: DONE 2026-11-09 —
        vision_check.py: runs the REAL extractor against stored images with
        known text (--fixtures) or the newest live photos (--live); exit 2 on a
        strike mismatch. First run: 3956 -> 7625/7635 OK, 3964 -> 7490/7500 OK,
        4047 chart -> no legs OK. Vision reads strikes correctly; it misread
        the credit on 3956 (3.45 vs stated), so premium still needs care.
        levels.json + store_levels()/recent_levels(): every captured level now
        persists with symbol/role/source, capped 800, readable by symbol+hours.
        Root repair: an unknown root is replaced from strike scale (>=10k NDX,
        1k-10k SPXW) or a quoted ticker, logged SYMFIX; parks only if unreadable.
        OPEN: nothing consumes recent_levels() yet — ladder wiring is next.
```

```
CASE:   a credit read off a screenshot anchors sizing AND P&L (3956 read 3.45
        against a true 5.70 on 10 lots)
LIMIT:  SYSTEM
WHY:    nothing cross-checked the extracted premium; a misread sizes the trade
        wrong and skews every mark after it.
RESOLVE: DONE 2026-11-09 — image-sourced credits are reconciled against the
        chain mid (spread_credit_mid). >25% apart -> the MID replaces the read,
        logged CREDITFIX with an alert. No quote -> if the credit is impossible
        on the width (>=95% of it, or ~0) the trade PARKS; otherwise it is
        marked unverified and forced to ONE contract. Text credits are
        unaffected (deterministic, no chain check). Pinned by
        test_credit_guard.py.
```
