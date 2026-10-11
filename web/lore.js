// Shared by index.html (the app) and about.html (the lore page): every real image we use, with its source.
// Images: Verity Wiki (verityminecraft.fandom.com, fan contributors), YouTube thumbnails (linked to each video),
// Know Your Meme. Verity is ThatMob's character; the song is Horror Skunx's. We're a fan parody.
const WIKI = (f) => `assets/wiki/${f}`;
const MEME = (f) => `assets/memes/${f}`;
const YT = (id) => `https://www.youtube.com/watch?v=${id}`;
const WIKI_URL = 'https://verityminecraft.fandom.com/';

const LORE = {
  intro: ["Hello! I'm Verity, your personal helper friend.", 'Ask me anything, I know everything.'],   // its real first lines in the series

  // Verity's faces, friendly -> unhinged (the order the series goes in)
  expressions: [
    ['verityneutral.webp', 'Neutral'], ['verityopen.webp', 'Helping'], ['happy-verity.webp', 'Happy to help'],
    ['veritycreepy.webp', 'Smiling (too much)'], ['veritycreepyopen.webp', 'Laughing at you'], ['veritydispleased.webp', 'Displeased'],
    ['veritydispleasedeyesopen.webp', 'You did WHAT'], ['verity-mad-sad.webp', 'Mad. Sad. Both.'], ['upsidedownverity.webp', 'Upside down'],
  ],

  monsters: [
    ['veritymonsterform.webp', 'Monster form'], ['true-form.webp', 'True form'], ['monsterveritynewopenmouth.webp', 'It opened its mouth'],
    ['wontletyouleave.webp', "Won't let you leave"], ['something-is-knocking-at-your-door.webp', 'Something is knocking at your door'],
    ['somethingisinsideyourhouse.webp', 'Something is inside your house'], ['verity-monster2.webp', 'Out in the field at night'],
    ['monstergrab.webp', 'Grab'], ['newveritytransparentbackground.webp', 'Waves hello'],
  ],

  // The -ity multiverse. flat/3d/monster are wiki images; about is ours to read (fan lore varies by creator).
  variants: [
    { name: 'Verity', color: '#f4c20d', img: 'verityneutral.webp', alt: 'veritymonsterform.webp', about: 'The original. Personal helper friend. Knows everything.' },
    { name: 'Falsity', color: '#1f5fff', img: 'falsity3d.webp', alt: 'falsity-monster.webp', about: 'The liar. The first big fan variant, by YouTuber xqree (June 17, 2026).' },
    { name: 'Cruelty', color: '#e01b1b', img: 'cruelty.webp', alt: 'cruelty-monster.webp', about: "The angry one, hostile to Verity. In our app it's what Verity turns into after three strikes." },
    { name: 'Lovity', color: '#ff4fb4', img: 'lovity3d.webp', alt: 'lovity-monster.webp', about: 'All affection, all romance, all the time.' },
    { name: 'Curiosity', color: '#2fd14b', img: 'curiosity.webp', alt: 'curiosity-monster.webp', about: 'Asks too many questions, even for a Verity.' },
    { name: 'Manipulity', color: '#8b2be2', img: 'manipulity.webp', alt: 'manipulity-monster.webp', about: 'The sneaky one. Do not trust the smile.' },
    { name: 'Eclipsity', color: '#111111', img: 'eclipsity.webp', alt: 'eclipsity-monster.webp', about: 'Lights out. Teeth on.' },
    { name: 'Herity', color: '#1e6b00', img: 'herity.webp', alt: 'scary-herity.webp', about: 'Grinning, green, and worse up close.' },
    { name: 'Triangle Verity', color: '#f1c40f', img: 'triangleverity.webp', alt: 'trianglemonsterform.webp', about: 'Verity, but a triangle. Yes, it has a monster form.' },
    { name: 'Moggity', color: '#e0a020', img: 'moggity.webp', about: 'Mogging. Jawline maxxed. Unbothered.' },
    { name: 'Stupidity', color: '#7d00c8', img: 'stupidity.webp', about: 'Eyes not aligned. Neither is anything else.' },
    { name: 'Powerity', color: '#00e5ff', img: 'powerity.webp', about: 'Glowing. Fully charged. Slightly radioactive.' },
    { name: 'Purity', color: '#e9e9e2', img: 'purity.webp', about: 'Pure. Suspiciously pure.' },
    { name: 'Rainbow Godity', color: '#ff00c8', img: 'rainbow-godity.webp', about: 'A rainbow star. Godlike, allegedly.' },
    { name: 'Variety', color: '#ff7a00', img: 'variety-3d.webp', about: 'Every color at once. Spins.' },
    { name: 'Larpity', color: '#22dd22', img: 'larpity.webp', about: 'Larping as Verity. Convincingly.' },
    { name: 'Timeity', color: '#2a9d4a', img: 'timeity.webp', about: 'Winks at the clock. Knows what time it is.' },
  ],

  timeline: [
    ['May 30, 2026', 'A box arrives', 'ThatMob uploads the first Verity episode: a player opens a box and meets a floating yellow smiley, voiced by JustWhispy, that introduces itself as a personal helper friend.', WIKI('verity-debut.webp')],
    ['Early on', '"What\'s 2 + 2?"', 'It helps: finds resources, gives coordinates, answers questions. Then it starts knowing things it shouldn\'t, like details of the player\'s real life.', WIKI('veritylab.webp')],
    ['Then', '"Something is coming in three days."', 'The smile stays. Everything else gets worse.', WIKI('something-is-knocking-at-your-door.webp')],
    ['July 8, 2026', 'The final episode', 'Verity drops the act and becomes a hostile humanoid monster. Three episodes, 22M+ views on the premiere in about two months.', WIKI('true-form.webp')],
    ['July 10, 2026', '"It\'s me, It\'s Verity"', 'Horror Skunx releases the song. 12M+ views in a month, then every TikTok and Reel for weeks.', MEME('hs_tVlKLpxyCBY.jpg')],
    ['June to Sep', 'The -ity multiverse', 'Fans make variants: Falsity (xqree, June 17), then Lovity, Cruelty, Curiosity and hundreds more. Any word ending in -ity is now a character.', WIKI('falsity3d.webp')],
    ['Summer 2026', 'Brainrot era', 'AI edits, Verity Tung, Roblox games, plushies, CG5 × Horror Skunx "your verity". A teacher tells HuffPost any yellow smiley is basically now Verity.', MEME('../verity-tung.webp')],
    ['Sep 27, 2026', 'Forbes explains it', "USA Today calls it the possible new 6-7. Reddit's r/hatethissmug calls it a content farm. Nobody can stop saying it.", WIKI('verity-plushs-in-box.webp')],
    ['Now', 'Verity Agent', 'You can FaceTime it. It sees your room, rates your dance, and if you get the capital of France wrong three times, you meet Cruelty.', 'assets/ours/verity-talk.png'],
  ],

  // 3D models used for the call avatar (verity/models.py), CC Attribution, from Sketchfab
  models3d: [
    ['Verity', 'c00lzer', 'https://sketchfab.com/3d-models/aa5225880f5445e79a425eea391c42fb'],
    ['Verity Open Mouth', 'c00lzer', 'https://sketchfab.com/3d-models/5ba54a0cbcb34b9fa2f85948bbbaa617'],
    ['Falsity', 'Astrix3D', 'https://sketchfab.com/3d-models/3661aadc8ff24c87af9a10c70839c2ec'],
    ['Verity Monster', 'c00lzer', 'https://sketchfab.com/3d-models/4a58bd4f61c14c089623a63c2e3138d7'],
  ],

  memes: [
    [MEME('hs_tVlKLpxyCBY.jpg'), "It's me, It's Verity (official song)", 'Horror Skunx', YT('tVlKLpxyCBY')],
    [MEME('hs_kErWhzq5gTc.jpg'), 'your verity', 'CG5 × Horror Skunx', YT('kErWhzq5gTc')],
    [MEME('hs_tJAqxdJ-hx8.jpg'), "It's me, It's Verity: behind the scenes", 'Horror Skunx', YT('tJAqxdJ-hx8')],
    [MEME('kym_veritycover.jpg'), "It's Me, It's Verity", 'Know Your Meme', 'https://knowyourmeme.com/memes/its-me-its-verity'],
    ['assets/verity.png', 'Verity in the original series', 'ThatMob, via Know Your Meme', 'https://knowyourmeme.com/memes/subcultures/verity-minecraft-arg'],
    ['assets/verity-tung.webp', 'Verity Tung', 'AI brainrot, TikTok', null],
    [WIKI('veritythumbnail.webp'), 'Verity and Mob', 'Verity Wiki', WIKI_URL],
    [WIKI('maxresdefault.webp'), 'Verity in Roblox', 'Verity Wiki', WIKI_URL],
    [WIKI('verity-plushs-in-box.webp'), 'The plushies arrived. wtf????', 'Verity Wiki', WIKI_URL],
    [MEME('yt_W8tODwHIKUI.jpg'), "Hey It's me, It's Verity: all versions", 'YouTube Shorts', YT('W8tODwHIKUI')],
    [MEME('yt_fKjwk7vBQiQ.jpg'), 'Meme vs original', 'YouTube Shorts', YT('fKjwk7vBQiQ')],
    [MEME('yt_YDgjVP4k7Pk.jpg'), 'The Verity meme keeps getting weirder', 'YouTube Shorts', YT('YDgjVP4k7Pk')],
    [MEME('yt_-PJ9vjL5VRI.jpg'), "How Walmart's Smiley became Verity", 'YouTube', YT('-PJ9vjL5VRI')],
    [MEME('yt_2b1hD9QKKDI.jpg'), 'Verity is terrifying', 'YouTube', YT('2b1hD9QKKDI')],
    [MEME('yt_rZYZKsDuvs8.jpg'), 'What is Verity?', 'YouTube', YT('rZYZKsDuvs8')],
    [MEME('yt_DxF3YjIR78k.jpg'), 'Do not trust this smiley face', 'YouTube Shorts', YT('DxF3YjIR78k')],
    [MEME('yt_MNnamEAqZ7k.jpg'), 'Verity got a little angry', 'YouTube Shorts', YT('MNnamEAqZ7k')],
    [MEME('yt_RQni02wpRuw.jpg'), 'Demons turned Verity evil', 'YouTube', YT('RQni02wpRuw')],
    [MEME('yt_5K3AS-CaNuk.jpg'), 'Godrity + Verity + Falsity + … = ???', 'YouTube', YT('5K3AS-CaNuk')],
    [MEME('yt_jmpTsn6C8AA.jpg'), 'Verity evolution: the variant mod', 'YouTube', YT('jmpTsn6C8AA')],
    [MEME('yt_78U0ERMRLy0.jpg'), 'Verity vs Falsity vs Cruelty', 'YouTube', YT('78U0ERMRLy0')],
    [MEME('yt_KOB5CNBO0-A.jpg'), 'The Verity Tapes', 'YouTube', YT('KOB5CNBO0-A')],
    [MEME('yt_XymLNSABdzo.jpg'), 'Cruelty monster vs Verity & Falsity', 'Dj GG, YouTube', YT('XymLNSABdzo')],
    [MEME('yt_FwCRNvxn6B4.jpg'), 'Verity vs Cruelty', 'YouTube', YT('FwCRNvxn6B4')],
    [MEME('yt_iVuUGHix9ek.jpg'), 'Verity + Falsity + Cruelty + Obscurity ❤️ Lovety', 'YouTube', YT('iVuUGHix9ek')],
    [MEME('yt_V5mgbajUAiY.jpg'), 'The Verity Multiverse', 'YouTube', YT('V5mgbajUAiY')],
    [MEME('yt_tg00u3NIVSo.jpg'), 'Verity, Falsity, Lovity, Curiosity vs Cruelity', 'YouTube', YT('tg00u3NIVSo')],
    [MEME('yt_Cr9JnA3y0rs.jpg'), 'How to draw the -ities', 'YouTube', YT('Cr9JnA3y0rs')],
    [MEME('yt_Nfux_mmrxJk.jpg'), 'Verity variants speed coloring', 'YouTube', YT('Nfux_mmrxJk')],
    ['assets/ity-chart.png', 'The fan -ity chart', 'Fan art', null],
  ],
};
