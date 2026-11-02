# Owner verification checklist

All boxes are **pending** until a person records evidence. Automated suites and
the optional offscreen smoke do not complete this checklist.

Record the Git commit, artifact SHA-256, date, OS/architecture, Anki/Python/Qt
versions, provider/model IDs, and pass/fail notes. Never include keys, outgoing
auth headers, private topics, or raw provider errors in evidence.

Use a new, empty, disposable Anki base folder. Close other Anki instances and
launch your Anki executable with `-b <absolute-empty-test-base>`; see the official
[startup options](https://docs.ankiweb.net/files.html#startup-options). Confirm
the selected base location before installing the artifact. Do not copy personal
profiles or sign in to sync. Use dedicated low-limit provider keys only for the
explicit live checks. Do not weaken browser security to get a pass.

- [ ] Install the `.ankiaddon` in this base on Anki 26.9.3. Restart. The first
  profile opens WayOom/setup; Cancel leaves Anki usable. Setup can be reopened.
  One provider is enough. Save, restart and confirm setup does not repeat.
  **AC-01/02/03/25.**
- [ ] On Windows, macOS and Linux, exercise protected save, replace, test, remove
  and profile isolation with dedicated keys. If unavailable, explicitly choose
  session-only storage and confirm it disappears after closing the profile.
  Inspect settings, `user_files`, logs and webview state without copying secrets
  into evidence. **AC-19; SEC-01/02/03/08.**
- [ ] Run the opt-in verifier separately for all five providers, then also use
  each through the real form. Record model IDs and outcomes; default IDs remain
  live-untested until the owner reviews results. Check rejected/expired key,
  unavailable model, quota/rate limit and disconnected network outcomes. Network
  failure must not invalidate a verified key. **AC-04/21/27/28/29; ARCH-03.**
- [ ] Check keyboard labels, Tab/Shift-Tab order, visible focus, inline errors
  and announced status with a screen reader. Verify readable light/dark themes
  and scrolling at the intended panel width. Enter 0, 101, a fraction, blank
  count/topic; none should send a provider request. Type while state updates
  arrive and confirm values/caret/focus stay intact. **AC-09; NFR-01/04.**
- [ ] Select a hierarchical normal deck by name; filtered decks are absent.
  Generate exactly 10 and inspect real reviewable cards. Create supplied-name
  and blank-name decks, including a collision suffix. Try 1 and 100. Existing
  cards and scheduling must remain unchanged. **AC-05/06/07/08.**
- [ ] Double-click Generate; only one job/batch appears. Hide the panel during
  generation/review and reopen it; the job continues and current state returns.
  Cancel during naming/cards, then switch profiles during another job; late
  responses must insert nothing. Adding disables Cancel and explains finishing.
  **AC-13/14/25.**
- [ ] Trigger a definitive mid-generation failure using a disposable setup.
  Inputs remain. Retry unchanged rechecks retained drafts and requests only the
  missing cards. Edit and restore an input; the retry starts from zero. Change
  the active provider/model/key and verify drafts are discarded. **AC-11/12/24.**
- [ ] Delete the selected deck before insertion and separately customize the
  owned note type in this disposable collection. No fallback destination or
  customization overwrite is allowed. Repair offers a replacement, then Retry
  works. Exercise safe write-failure/recovery scenarios using the automated
  fault fixtures rather than damaging a real database. **AC-15/16.**
- [ ] After success, View cards opens exactly that job's cards. Anki Undo removes
  its whole batch in one action while preserving prior cards/scheduling. Review
  literal HTML/script-looking text; it cannot execute or call bridge actions.
  Generate the same topic into the same deck again and inspect duplicates.
  **AC-17/18/22/23.**
- [ ] Exercise an interrupted-result/restart scenario in the disposable base.
  Committed work is not reinserted. Unresolved evidence blocks generation;
  inspection and acknowledgment preserve existing cards. **AC-20.**
- [ ] Reinstall an updated artifact under the same package identity `wayoom`.
  Keep `user_files`. Both profiles retain settings, visibility, key references
  and recovery evidence; setup does not repeat for available saved keys.
  **AC-26.**
- [ ] Collect the quality sample, fill human ratings and review every reported
  correctness/relevance/clarity/duplicate-concept concern. Confirm at least 80%
  difficulty match at each level under the documented sampling policy. A script
  summary alone is not a release approval. **AC-10; NFR-08.**
- [ ] Before deleting this installation, Remove all keys — every profile and
  confirm deletion on the actual OS stores. Revoke the dedicated keys at each
  provider. Uninstall alone may leave OS credentials. **AC-30; SEC-09/11.**
- [ ] Review dependencies/licensing and require successful Windows/macOS/Linux
  CI runs. Record any exclusions and compare artifacts before approving release.
  **SEC-10; PKG-03.**
