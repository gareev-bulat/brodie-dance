// Booking rules for FaceTime testing slots (see .github/workflows/testing-slots.yml).
const fs = require('fs');
const MAX_PER_TEAM = 2;
const CAP = 3;                                   // teams per slot, same as docs/slots/index.html
const MARK = '<!-- slot-bot -->';
const PAGE = 'https://getbrodie.com/ivyhacks/slots';

const GARY = '577833469842030612';               // Discord user who provisions the FaceTime line

// Ping Gary in #staff-chat (webhook in secret DISCORD_LINE_WEBHOOK). Only Gary can be mentioned; team text cannot ping anyone.
async function pingGary(text) {
  const hook = process.env.DISCORD_LINE_WEBHOOK;
  if (!hook) return console.log('DISCORD_LINE_WEBHOOK not set, skipping Discord ping');
  const clean = text.replace(/@/g, '@\u200b');
  const r = await fetch(hook, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content: `<@${GARY}> ${clean}`.slice(0, 1900), allowed_mentions: { parse: [], users: [GARY] } }) });
  if (!r.ok) console.log('Discord ping failed', r.status, await r.text());
}

function field(body, name) {
  const m = (body || '').match(new RegExp('### ' + name + '\\s*\\n+([^\\n]+)'));
  const v = m ? m[1].trim() : '';
  return v === '_No response_' ? '' : v;
}

module.exports = async ({ github, context }) => {
  const { owner, repo } = context.repo;
  const issue = context.payload.issue;
  const { slots } = JSON.parse(fs.readFileSync('docs/slots/slots.json', 'utf8'));
  const slotLabel = field(issue.body, 'Slot');
  const team = field(issue.body, 'Team name');
  const slot = slots.find(s => s.label === slotLabel);
  const who = () => `**${team}** (agent \`${field(issue.body, 'Agent name') || '?'}\`, Discord ${field(issue.body, 'Discord handle') || '?'}, ${field(issue.body, 'Testing from') || '?'})`;

  if (context.payload.action === 'closed') {
    const names = (issue.labels || []).map(l => l.name);
    if (names.includes('booked') && !names.includes('slot-rejected') && slot && new Date(slot.end) > new Date())
      await pingGary(`line booking cancelled: ${who()} gave up **${slot.label}**. ${issue.html_url}`);
    return;
  }

  const say = body => github.rest.issues.createComment({ owner, repo, issue_number: issue.number, body: MARK + '\n' + body });
  const reject = async why => {
    await say(`❌ ${why}\n\nPick another open slot at ${PAGE} and open a new booking.`);
    await github.rest.issues.addLabels({ owner, repo, issue_number: issue.number, labels: ['slot-rejected'] });
    await github.rest.issues.removeLabel({ owner, repo, issue_number: issue.number, name: 'booked' }).catch(() => {});
    await github.rest.issues.update({ owner, repo, issue_number: issue.number, state: 'closed', state_reason: 'not_planned' });
  };

  if (!slot) return reject(`I couldn't read a valid slot from this issue (got "${slotLabel || 'nothing'}").`);
  if (!team) return reject('The team name is missing.');
  if (new Date(slot.end) < new Date()) return reject(`**${slot.label}** has already passed.`);

  const open = await github.paginate(github.rest.issues.listForRepo, { owner, repo, state: 'open', labels: 'slot', per_page: 100 });
  const others = open.filter(i => i.number !== issue.number && i.title.startsWith('[slot]'));
  const holders = others.filter(i => field(i.body, 'Slot') === slot.label && i.number < issue.number);
  if (holders.some(i => field(i.body, 'Team name').toLowerCase() === team.toLowerCase()))
    return reject(`**${team}** already has **${slot.label}**.`);
  if (holders.length >= CAP) return reject(`**${slot.label}** is full (${CAP} teams: ${holders.map(i => field(i.body, 'Team name') + ' #' + i.number).join(', ')}).`);

  const now = new Date();
  const mine = others.filter(i => {
    const s = slots.find(x => x.label === field(i.body, 'Slot'));
    return field(i.body, 'Team name').toLowerCase() === team.toLowerCase() && s && new Date(s.end) > now && i.number < issue.number;
  });
  if (mine.length >= MAX_PER_TEAM) return reject(`**${team}** already has ${MAX_PER_TEAM} upcoming slots (${mine.map(i => '#' + i.number).join(', ')}). Use one first, or close one to swap.`);

  const title = `[slot] ${slot.label} · ${team}`;
  if (issue.title !== title) await github.rest.issues.update({ owner, repo, issue_number: issue.number, title });
  await github.rest.issues.addLabels({ owner, repo, issue_number: issue.number, labels: ['slot', 'booked'] });

  const comments = await github.paginate(github.rest.issues.listComments, { owner, repo, issue_number: issue.number, per_page: 100 });
  if (comments.some(c => c.body && c.body.startsWith(MARK) && c.body.includes('✅'))) return;
  await pingGary(`provision the FaceTime line: ${who()} booked **${slot.label}** (${holders.length + 1}/${CAP} teams). ${issue.html_url}`);
  const agent = field(issue.body, 'Agent name') || 'team-yourname-agent';
  const mates = holders.map(i => `${field(i.body, 'Team name')} (${field(i.body, 'Discord handle') || '#' + i.number})`);
  await say(`✅ **${slot.label}** is yours, ${team}.${mates.length ? ` You share it with ${mates.join(', ')}: agree an order on Discord, up to ${CAP} teams per slot.` : ` Up to ${CAP} teams can share this slot.`}

**When your slot starts** (two terminals in your agent folder):
\`\`\`
npm start
chert doctor && chert dev --agent ${agent}
\`\`\`
Wait for your agent to join its waiting room, then call the shared address on **FaceTime Audio** (FaceTime Video for avatars).

**When it ends:** hang up, wait for *Call ended*, press **Ctrl+C** in the Chert terminal, wait for *Stopped*, then tell the next team the line is free. Hanging up alone does not release the line.

Can't make it? Close this issue and the slot opens back up.`);
};
