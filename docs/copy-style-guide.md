# SuperDL copy style guide (voice & tone)

All user-visible copy (both consoles' locales JSON, the backend `core/messages.py`) follows this guide; banned words are enforced in CI by `scripts/check-copy-banned.sh`.
Tone: restrained, concrete, actionable; facts and consequences, not emotion or sales pitch.

## Sentence rules

1. **Title = noun phrase** ("Instances", "Billing"), no explanatory parentheses or subtitle; term definitions go in a tooltip.
2. **Button = verb + object**, ≤ 6 characters in zh-CN and about three words in en-US ("Create and start", "Generate enrollment command"); dangerous actions carry their object ("Release instance", not "Confirm").
3. **Empty state = one sentence + one action** ("No instances yet" + "Go to the market").
4. **A disabled tooltip states the precondition** ("Stop the instance before releasing it").
5. **Error = fact + next step** ("The data disk is mounted; release the instance first"); the backend message_key is the single source of constraint copy, the frontend does not rewrite it.
6. **Confirmation = consequence first** ("Force-stop this instance? The tail bill is settled immediately and the user is notified").
7. **Amounts / durations / dates go through the format functions**; copy never concatenates a bare currency symbol (use template keys such as `common.gbMonthPrice`).

## Prohibitions

- **Implementation details stay inside**: polling intervals, patrol periods, mechanism words such as outbox / poller / handler, repository paths, component versions.
  - **Exception: admin operations pages** (cluster, nodes, platform configuration) address operators, so K8s object names, namespaces, image and component versions, condition reasons and kubectl commands appear verbatim, without "translation". Still enforced: the banned-word list; patrol periods and polling intervals stay inside (they are implementation rhythm, not cluster facts); **the front of a diagnostic panel shows only checkable numbers and identifiers (x/y, versions, object names, addresses, durations), no adjectives**; business interpretation (stock, sellability) belongs to the capacity view, not the component view.
- **Internal jargon stays inside**: "post an adjustment", "port pool" and the like become actionable wording.
- **No empty promises**: unimplemented features are not written about; reserved features uniformly say "coming soon".
- **No slogan moulds**: the "A · B · C" triplet is limited to the hero, once.
- No exclamation marks, no emoji, no honorific 您 in zh-CN, no time promises ("up in one minute"). <!-- cjk-ok -->
- Banned words (CI-enforced, zh-CN): 智能、强大、轻松、一键、全方位、高效、极速、助力、赋能、颠覆、极致; 「一键加入」 and 「一键添加」 are allow-listed. <!-- cjk-ok -->

## i18n companion rules

- New copy lands in zh-CN and en-US together; the English is rewritten with its own word order (plurals via `_one/_other`).
- Keys with a `count` parameter get plural variants on the en side; run `pnpm i18n:write` under `apps/web` / `apps/admin` before committing to fill them in.
- Platform-configuration field names / provider names / risk copy and policy parameter names also live in the locales (dynamically resolved keys must be listed in `preservePatterns` of `i18next.config.ts`); only regulatory registration names without an official English name (ICP filing, public security network filing) keep the Chinese term in en with an English explanation, allowed one by one through `allowCjkInEn` in `locales.test.ts`.

## Glossary

- Common technical abbreviations stay as-is in both languages: ID, IP, CUDA, Python, GPU, vCPU, SSH, API Key, JupyterLab, diff%.
- The brand wordmark `SuperDL` is not translated; the allow-listed zh-CN phrases for "join" / "add" are used verbatim.
