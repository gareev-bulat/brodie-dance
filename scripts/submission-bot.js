// Rules for submissions (see .github/workflows/submissions.yml).
const DEADLINE = new Date('2026-10-11T18:00:00-04:00');
const MARK = '<!-- submission-bot -->';
const PAGE = 'https://getbrodie.com/ivyhacks/submit';
const POST_HOSTS = ['instagram.com', 'tiktok.com', 'x.com', 'twitter.com', 'linkedin.com', 'youtube.com', 'youtu.be'];

function field(body, name) {
  const m = (body || '').match(new RegExp('### ' + name + '\\s*\\n+([\\s\\S]*?)(?=\\n### |$)'));
  const v = m ? m[1].trim() : '';
  return v === '_No response_' ? '' : v;
}
function url(v) { try { const u = new URL(v); return /^https?:$/.test(u.protocol) ? u : null; } catch { return null; } }
const host = u => u.hostname.replace(/^(www\.|m\.)/, '');

module.exports = async ({ github, context }) => {
  const { owner, repo } = context.repo;
  const issue = context.payload.issue;
  const n = issue.number;
  const team = field(issue.body, 'Team name');
  const repoUrl = url(field(issue.body, 'GitHub repo'));
  const postUrl = url(field(issue.body, 'First post link'));
  const now = new Date();

  const comments = await github.paginate(github.rest.issues.listComments, { owner, repo, issue_number: n, per_page: 100 });
  const mine = comments.filter(c => c.body && c.body.startsWith(MARK));
  const say = async body => {
    body = MARK + '\n' + body;
    const last = mine[mine.length - 1];
    if (last && last.body === body) return;           // don't repeat ourselves on every edit
    if (last) return github.rest.issues.updateComment({ owner, repo, comment_id: last.id, body });
    return github.rest.issues.createComment({ owner, repo, issue_number: n, body });
  };
  const label = (add, remove = []) => Promise.all([
    add.length && github.rest.issues.addLabels({ owner, repo, issue_number: n, labels: add }),
    ...remove.map(name => github.rest.issues.removeLabel({ owner, repo, issue_number: n, name }).catch(() => {})),
  ]);

  // one per team: the earliest open submission with the same name wins
  if (team) {
    const open = await github.paginate(github.rest.issues.listForRepo, { owner, repo, state: 'open', labels: 'submission', per_page: 100 });
    const first = open.filter(i => i.title.startsWith('[submission]') && i.number < n)
      .find(i => field(i.body, 'Team name').toLowerCase() === team.toLowerCase());
    if (first) {
      await say(`❌ **${team}** already submitted in #${first.number}. Edit that issue instead (… menu → Edit). Closing this one.`);
      await label(['duplicate'], ['complete']);
      return github.rest.issues.update({ owner, repo, issue_number: n, state: 'closed', state_reason: 'duplicate' });
    }
  }

  const problems = [];
  if (!team) problems.push('Add your **team name**.');
  if (!field(issue.body, 'Team members')) problems.push('List your **team members**.');
  if (/[\w.+-]+@[\w-]+\.[\w.]+/.test(field(issue.body, 'Team members'))) problems.push('Remove **email addresses** from team members; this issue is public. Use GitHub or Discord handles.');
  if (!repoUrl || host(repoUrl) !== 'github.com' || repoUrl.pathname.split('/').filter(Boolean).length < 2)
    problems.push('**GitHub repo** must be a full link like `https://github.com/your-team/brodie-starter`.');
  if (!postUrl || !POST_HOSTS.some(h => host(postUrl) === h || host(postUrl).endsWith('.' + h)))
    problems.push('**First post link** must be a full IG, TikTok, X, LinkedIn or YouTube link.');

  // a public repo is required for judging
  if (repoUrl && host(repoUrl) === 'github.com' && !problems.some(p => p.includes('GitHub repo'))) {
    const [o, r] = repoUrl.pathname.split('/').filter(Boolean);
    try { const { data } = await github.rest.repos.get({ owner: o, repo: r.replace(/\.git$/, '') }); if (data.private) problems.push('Your **GitHub repo** is private. Make it public so judges can open it.'); }
    catch { problems.push(`I can't open **${repoUrl.href}**. Check the link, and make sure the repo is public.`); }
  }

  const title = `[submission] ${team || 'Untitled team'}`;
  if (issue.title !== title) await github.rest.issues.update({ owner, repo, issue_number: n, title });

  const late = now > DEADLINE;
  const lateLabel = late ? (context.payload.action === 'opened' ? 'late' : 'edited-after-deadline') : null;

  if (problems.length) {
    await label(['needs-fixes', ...(lateLabel ? [lateLabel] : [])], ['complete']);
    return say(`⚠️ Almost there. Edit this issue (… menu → Edit) to fix:\n\n${problems.map(p => '- ' + p).join('\n')}\n\nI re-check every time you save.`);
  }
  await label(['complete', ...(lateLabel ? [lateLabel] : [])], ['needs-fixes']);
  await say(`✅ **${team}** is submitted${late ? ', after the 6 PM deadline. Organizers will decide on late entries.' : '.'}\n\nYou can keep editing this issue until **Sun Oct 11, 6 PM ET**. All submissions: ${PAGE}`);
};
