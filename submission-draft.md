# IvyHacks Brodie submission (draft, not committed)

The form is a **GitHub issue**: https://getbrodie.com/ivyhacks/submit (the "Submit your build" issue template on seraphimlabs-dev/brodie-starter). Due **Sun Oct 11, 6 PM ET**; one per team, editable until then (… menu → Edit). The issue is **public**: no emails or phone numbers, handles only. The **First post link** must be an actual post (not a profile or channel page) on **IG, TikTok, X, LinkedIn or YouTube**; the bot checks the host and rejects anything else, and it checks that the repo is public.

Organizer rules from Discord (James, 11:05 and 11:28 AM):
- The agent MUST follow the character kit (https://brodie-character-kit.vercel.app/) to be eligible to win: the face on the call is Brodie (cream #EFE7DA, never pink, tank with readable BRODIE lettering, one costume). Our demo character is **Brodie the dance coach** (headphones costume, built from the kit's reference art, passes the kit's `check_cream.py`).
- **Rename the fork** in GitHub Settings to `ivyhacks-<team>` (lowercase, dashes), e.g. `ivyhacks-verity-agent`. Only the repo owner (zxpeng2007) can: Settings → General → Repository name. Old links redirect; teammates should `git remote set-url origin https://github.com/zxpeng2007/ivyhacks-<team>.git`.
- Social presence = a stylized Brodie: profile picture `web/assets/brodie-pfp.png` (the kit's pfp), posts and the demo video built from the kit (the demo video shows Brodie's face on the call, so it qualifies).
- Keep `.env.local` and keys out of git (it is gitignored; the history was checked: no keys).
- Closing ceremony 2 PM, Huntsman 265: every team demos what they have, rough is fine. Have the fork link ready.

**Team name:** Verity Agent  <!-- change to the registered team name -->

**Team members** (one per line, name and GitHub or Discord handle; no emails):
```
Jason, @<handle>
Bulat, @<handle>
Steven, @<handle>
<friend>, @thanos6121B
```

**GitHub repo:** https://github.com/zxpeng2007/brodie-starter  <!-- becomes https://github.com/zxpeng2007/ivyhacks-<team> after the rename -->
(public fork of seraphimlabs-dev/brodie-starter; `main` is the Brodie build.)

**First post link:** <IG / TikTok / X / LinkedIn / YouTube URL of the first post>  <!-- required; a post link, the bot rejects other hosts -->

**What your Brodie skill does:**
FaceTime **Brodie the dance coach**: he teaches a real TikTok dance one pose at a time. The call shows your own camera with your body's nodes drawn on it and a ghost skeleton of the pose to copy; Brodie's pose model (MediaPipe on Brodie's worker) scores the match live and he does not move on until you hold the pose, gives one tip naming the body part that is off when you are stuck, rates everything out of ten (never a nine), gets madder with every miss up to drill-sergeant mode, and wraps up with how many poses you nailed. The same build also has the Verity-meme characters ("It's me, it's Verity": outfit roasts, -ity verdicts, three strikes to Cruelty) as extra skins on the same worker. Everything runs on Brodie's own Gemini voice worker (`facetime.worker --voice gemini --dance`) with a persona, Brodie handlers and a live face track, from the web app or a FaceTime call.
