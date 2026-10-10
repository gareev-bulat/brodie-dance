// Booking rules for FaceTime testing slots (see .github/workflows/testing-slots.yml).
const fs = require('fs');
const MAX_PER_TEAM = 2;
const MARK = '<!-- slot-bot -->';
const PAGE = 'https://seraphimlabs-dev.github.io/brodie-starter/slots/';

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
  const holder = others.find(i => field(i.body, 'Slot') === slot.label && i.number < issue.number);
  if (holder) return reject(`**${slot.label}** is already booked by #${holder.number} (${field(holder.body, 'Team name')}).`);

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
  const agent = field(issue.body, 'Agent name') || 'team-yourname-agent';
  await say(`✅ **${slot.label}** is yours, ${team}.

**When your slot starts** (two terminals in your agent folder):
\`\`\`
npm start
chert doctor && chert dev --agent ${agent}
\`\`\`
Wait for your agent to join its waiting room, then call the shared address on **FaceTime Audio** (FaceTime Video for avatars).

**When it ends:** hang up, wait for *Call ended*, press **Ctrl+C** in the Chert terminal, wait for *Stopped*, then tell the next team the line is free. Hanging up alone does not release the line.

Can't make it? Close this issue and the slot opens back up.`);
};
