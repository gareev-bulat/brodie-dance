# IvyHacks Brodie submission (draft, not committed)

The form is a **GitHub issue**: https://getbrodie.com/ivyhacks/submit (the "Submit your build" issue template on seraphimlabs-dev/brodie-starter). Due **Sun Oct 11, 6 PM ET**; one per team, editable until then (… menu → Edit). The issue is **public**: no emails or phone numbers, handles only. The **First post link** must be an actual post (not a profile or channel page) on **IG, TikTok, X, LinkedIn or YouTube**; the bot checks the host and rejects anything else, and it checks that the repo is public.

**Team name:** Verity Agent  <!-- change to the registered team name -->

**Team members** (one per line, name and GitHub or Discord handle; no emails):
```
Jason, @<handle>
Bulat, @<handle>
Steven, @<handle>
<friend>, @thanos6121B
```

**GitHub repo:** https://github.com/zxpeng2007/brodie-starter
(public fork of seraphimlabs-dev/brodie-starter; `main` is the Brodie build, PRs #1 to #4 merged. Open one more PR from `jason` so `main` has the Dancity lesson before the deadline.)

**First post link:** <IG / TikTok / X / LinkedIn / YouTube URL of the first post>  <!-- required; a post link, the bot rejects other hosts -->

**What your Brodie skill does:**
**Dancity** is a smiley "-ity" meme character you FaceTime for a dance lesson: it counts you in, plays 8 counts of a real TikTok dance (the creator's clip mirrored on the call with the music and the count), judges your moves with Brodie's pose model plus a dance judge we trained, gives one tip and runs the part again, getting angrier with every miss up to its red Cruelty form, then the whole dance and an honest wrap-up. **Verity**, the main "It's me, it's Verity" character, sees you on camera, roasts your outfit and gives you your "-ity" verdict; three strikes turn it into Cruelty until you apologise. Every character runs on Brodie's own Gemini voice worker (`facetime.worker --voice gemini`) with a persona, Brodie handlers and a live face track, from the web app or a FaceTime call.
