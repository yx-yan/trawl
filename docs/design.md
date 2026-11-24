# Design notes

## 1. Built like a client delivery

trawl does a real job (my own FDE search) and is also a portfolio piece for FDE roles. So
each part is built the way I'd build it for a customer: integrations through documented
public APIs, a database that keeps everything it saw, tests against recorded responses, a
Docker deployment, and a person approving anything that acts in the world.

## 2. Public board APIs only

Greenhouse, Lever and Ashby publish every company's open jobs as JSON, and MyCareersFuture
(Singapore's national job portal) has a public search API. That covers most AI startups and
the Singapore market without scraping. Large employers (banks, NVIDIA, Salesforce, Grab) are on
Workday or SmartRecruiters: Workday's careers sites read their jobs from a JSON endpoint
(`/wday/cxs/<tenant>/<site>/jobs`) that trawl reads the same way, searched for Singapore, and
SmartRecruiters has a public postings API filtered by country. LinkedIn is never scraped: its terms forbid it and
accounts get banned. LinkedIn jobs will come in through its alert emails or a pasted link.

Each connector is one function, `fetch(http, key) -> list[Posting]`, so adding a board means
adding one module. Lists without descriptions (MyCareersFuture, Workday, SmartRecruiters) come
with a `describe` that `process` calls only for postings the title rules let through: of about
1,200 Singapore postings from the Workday and SmartRecruiters companies added at once, about 290
needed one. A board that fails is reported and skipped; one company changing its
board name must not stop the daily run.

## 3. Keep every posting in a wanted location, not only title matches

The same work hides under many titles ([roles.md](roles.md)), so every posting in Singapore
is stored and judged by its description, not by whether its title looks right.

## 4. Same job, several boards

A company can post the same role on its own board and on MyCareersFuture, where the name is
the registered company ("ANTHROPIC SINGAPORE PTE. LTD."). A fingerprint of normalized company
and title links the two; the digest sends one copy (preferring the company's own board) and
never sends a fingerprint again once any copy was sent.

## 5. Nothing is submitted without approval

Planned for M3, decided now: trawl may pre-fill an application, but submitting waits for a
person to approve that exact application, and every step is written to an audit log.
Sensitive answers (phone number, visa status) come from an encrypted store and are never put
in a model prompt.

## 6. The model reports facts; rules decide

Whether a job suits a candidate depends on things that change (their graduation date, how much
experience counts as too much, whether they need a work pass), while what a posting says doesn't.
So the model never answers "should I apply?". It fills in `JobFacts` (`src/trawl/extract.py`): track, seniority,
minimum years, employment type, agency, work-authorization terms, graduation window, location,
PhD, required languages, whether it writes code. Plain rules in `src/trawl/decide.py` turn those
facts and the candidate's criteria into a verdict with reasons. The criteria are personal, so they
live in `~/.trawl/criteria.toml`, outside the repo; [`criteria.example.toml`](../criteria.example.toml)
shows the format, and the code's defaults are neutral.

This buys three things. Every rejection has a reason a person can read ("needs 5+ years",
"citizens / PR only"), and the digest tallies them. Changing the criteria is
`trawl process --redecide`, with no model calls. And the facts are about the job, not me, so the same extraction would serve a second
candidate unchanged.

Two lessons from real postings shaped the schema:

- **Preference is not a requirement.** "Particularly interested in applicants with a current right
  to work in Singapore" first came back as citizens-only and dropped a new-grad role that was open
  to sponsored candidates.
  `prefers_local` is now its own value: a flag in the digest, never a reason to reject.
- **Graduation windows are dates, not prose.** "Summer 2027 intern, graduating Dec 2027 - Jun 2028"
  was first kept as text, which no rule could check. The model now returns `graduation_from` /
  `graduation_to` as YYYY-MM, with a stated mapping for vague dates ("late 2027" -> 2027-09), and
  a validator turns the string "null" the model sometimes writes into a real null.

## 7. Cheapest checks first

Most postings are rejected without a model call: a senior title, a contract, an agency (SSIC
78xxx on MyCareersFuture), pay below the S Pass minimum, too many required years. MyCareersFuture's
search results have no description, so its detail is fetched only for postings that survive the
first rules, and the rules run again with the agency code and years it adds. On the first real
run, 696 of about 900 postings stopped at the rules; the model read 203.

Pay matters only as a visa fact: a full-time job paying under the S Pass minimum can't sponsor a
pass. Internships are exempt, since students intern on their Student's Pass.

DeepSeek's thinking mode refuses a forced `tool_choice`, so extraction runs with thinking off; it
is a lookup, not a reasoning task. A posting whose call fails keeps no verdict and is retried on
the next run.

## 8. Evals: hand-checked facts from real postings

`evals/cases.json` holds snapshots of real postings (they change and close), each with the facts I
checked by reading it and whether it should get through for a test candidate defined in the eval. `python evals/run_evals.py` runs them
against the live model; it caught the two lessons in section 6 and a "late 2027" that came back
once as 2027-01. Offline tests use a fake client and cover the rules exhaustively.

## 9. Matching the résumé, and what to learn next

Eligible jobs (a few a day, not hundreds) are scored against the résumé: a 0-100 score, up to
three strengths, up to five gaps, and the angle to lead with. The digest sorts by score and shows
strengths and gaps under each job.

- **The résumé is the profile.** trawl reads the résumé as it is on `origin/main` of a separate,
  private résumé repo: merged means approved, and local drafts never leave the machine. Another
  agent (Solfish) is the only writer of the résumé, through pull requests; trawl only reads it.
- **Contact details never reach the model.** Everything above the first `\section` (name, phone,
  email) and every LaTeX comment is dropped before the text is sent.
- **Scores are tied to a résumé hash.** When the résumé changes, every eligible job is scored
  again, and a job already sent whose score rose by 10 or more comes back under "Better fits since
  your résumé changed".
- **Gaps are counted, so they must be canonical.** The model is asked for one lowercase skill per
  item, and a validator splits "go/java" into two and cuts long lists instead of failing the call.
  `trawl gaps` (sent every Monday) ranks gaps across the open eligible jobs, FDE jobs counting
  double: a reading of the market that says what to build next, which lands on the résumé, which
  rescores the jobs.
- **Each requirement, with both sides quoted.** `compare.py` is a second call, apart from scoring so
  scores don't move: it lists up to 15 requirements (must or nice to have) and quotes the words of
  the posting and of the résumé. Code checks the quotes: a requirement the posting doesn't state is
  dropped, and a claim the résumé's words don't support goes down one step (have, partial,
  missing). The desktop app shows it as a table per job.
- **Personal files stay out of this public repo.** The résumé and the criteria live in `~/.trawl`
  on each machine; `deploy/deploy.sh profile` copies them to the server.

## 10. Tailoring the résumé: the model chooses, it never writes

Each good fit (score 60+) about to go out in the digest gets its own résumé, sent as a PDF right
after the digest with what was changed and why. `trawl tailor <id>` does the same for any job.

- **Choices, not a document.** The model sees the résumé (from the first section; no contact
  details) with an id on every entry, bullet and skill row, and answers with choices: the summary,
  which bullets to keep in what order and how each is worded, the order of entries and skill rows.
  Sections without ids (education, awards) can't change, and the LaTeX is rebuilt from the master's
  own source, so the header and layout are exactly the master's.
- **Every reworded bullet is held to its source.** A number not in that bullet, or a name (PyTorch,
  AWS, Kubernetes: a word with a capital that doesn't start a sentence, an inner capital or a digit)
  not in the entry or the skills, is a made-up fact: that bullet goes back to its original wording,
  and the digest says so. The summary is held to the whole résumé and may name the job it's for.
- **One page, cut from the bottom.** While XeLaTeX reports a second page, the last bullet of the
  longest entry goes (the model put the least relevant last), and the digest says which.
- **What changed is counted, not reported.** The model's own notes said it had dropped sections it
  can't touch, so the digest lists the real edits (reworded, left out, spare used, reordered),
  computed by comparing with the master, above the model's reasons.
- **Spare bullets.** True things that don't fit on the page live in `bank.toml` next to the résumé,
  under the same review; the model may swap them in.
- **Tied to the résumé.** A tailored résumé is kept per job and per hash of the résumé and bank; a
  new résumé means new tailored versions for jobs not yet sent. PDFs live in Postgres, so the
  container needs no writable volume. The master résumé is never written: Solfish stays its only
  writer.

## 11. Form answers: the model says what a question asks for, code answers it

For jobs on Greenhouse, Ashby and Lever, trawl reads the application form and drafts every answer,
grouped as needs you / drafted / filled / skipped.
Greenhouse publishes its forms (`?questions=true`). Ashby doesn't document an endpoint, but its job
page reads the form from a public GraphQL endpoint and trawl reads it the same way; if that
changes, the form is skipped and the résumé still goes out. Lever has no endpoint for its
questions, but its apply page is plain HTML: the standard fields are labelled inputs, and each block
of custom questions carries its own definition as JSON in a hidden `baseTemplate` input (type, text,
options, required), so a plain GET reads the whole form. MyCareersFuture applies through the
candidate's own signed-in profile, so there is no form to draft.

- **The model never sees personal answers.** It reads the questions, the résumé without contact
  details, and the posting, and for each question returns a kind: a profile field (by name only),
  an open question (with a draft), a consent, or skip. Code fills profile fields from
  `~/.trawl/profile.enc`. Text values (phone, email) aren't stored with the draft, only the field
  that fills them; for choices, code picks the option: "2027-06" matches "May - July 2027", "Q3"
  and "Summer" by month, a yes/no by the profile's boolean.
- **The profile is encrypted, with its key kept apart.** Fernet, key in the trawl checkout's `.env`,
  file in `~/.trawl`; `trawl profile edit` decrypts into a private temporary file for the editor.
  `trawl profile show` lists which fields are set, never their values.
- **Consents are the person's.** "I confirm the information is true", privacy notices and
  "we record interviews with an AI notetaker" wait for the person, whatever the model says: a
  pattern on the label overrides it. When filling (section 12), trawl gives only the kinds the person
  has opted into in their profile (`agree_privacy`, `agree_truthful`, `agree_recording`); any other
  consent, such as terms of service, still waits.
- **Demographic questions come only from the profile**, or the "decline" option.
- **Some questions are decided by their words, not the model.** The model once answered "High
  School Name" with the candidate's university, and left "Are you a Singapore citizen?" unanswered.
  Now a yes/no question about citizenship or permanent residency is answered from the profile's
  countries (Yes only when the question names one), and a question about high school only from
  the high-school fields; forms drafted earlier are corrected the same way when filled. Options
  that are bare years match a YYYY-MM date by year, and "Company website" matches the one option
  that is the company's website ("Palantir Website").
- **Drafts are checked like bullets.** A number or name in neither the résumé nor the posting is
  flagged on the draft (the draft can't be put back the way a bullet can). Acronyms of names in
  the source count as known: "FYP" for "Final Year Project".
- **Nothing here submits.** Every "needs you" must be answered before M3 can submit, and M3
  submits only after the person approves that exact form.

## 12. Submitting: trawl fills, the person approves, trawl clicks

`trawl apply <id>` fills a Greenhouse or Ashby form in Chrome and submits it when the person types
`submit`. It runs on their own machine against the server's database (`deploy.sh tunnel`).

- **No API for candidates.** Greenhouse, Lever and Ashby each have an "apply" endpoint, but it
  takes the employer's API key. A candidate applies through the web form, so trawl drives one, the
  way the person would.
- **A visible browser on the person's machine.** They watch it fill, finish what trawl leaves and
  answer any challenge the page puts up; trawl never tries to get past one. Chrome keeps its own
  profile in `~/.trawl/browser`. The server has no browser and needs none: Playwright is the
  optional `apply` extra.
- **The page names fields the way the API does.** Both boards give each field the id their API
  lists (`question_68860631`, a UUID path on Ashby), so the answers drafted from the API (section
  11) fill the page directly. On Greenhouse, selects are react-select comboboxes, and a few fields
  exist only on the page (phone country, city, school, degree, picked by level: "Master of Science"
  is "Master's Degree") and are filled from the profile when they're there. On Ashby, every choice
  is a labelled radio button or checkbox.
- **Each board names its Submit button.** Ashby's page has résumé-upload buttons of type `submit`
  before the real one; "the first submit button" would upload instead of apply.
- **Never the first of several options.** A choice is the exact option, or the only one containing
  the value. Typing "Nanyang" into the school picker lists a polytechnic before the university;
  that is left for the person, not guessed.
- **Approval is of the filled form.** Typing `submit` is refused while a required field is empty
  (react-select leaves its input empty after a choice, so a combobox counts as filled when its
  control shows one). Then trawl hashes every field's content, screenshots the page and only then
  clicks Submit. Consents are ticked only for the kinds the profile opts into, including boxes only
  the page shows (Greenhouse's GDPR box turns required once a demographic question is answered, so
  trawl looks for them after filling). The person sees them ticked before approving, and the audit
  log names each one.
- **A required GPA means skip.** If the form requires a GPA and the profile has none, `trawl apply`
  refuses before opening a browser and logs the skip.
- **What happened is read from what changed.** The form page can carry the job description, whose
  "thank you for your interest" proves nothing; a submission is confirmed only when the form is gone
  or the page moved to a confirmation. New error messages mean the form refused it. The browser's
  own validation doesn't: Greenhouse marks every box of a checkbox group required and submits from
  script past it, so on the first real submission the unticked boxes read as a refusal while the
  page was asking for the emailed code. Those checks now only explain a Submit after which nothing
  happened; a security code is typed into the page by the person. When trawl can't tell, it
  asks, and records who confirmed.
- **An audit log without the values.** Every step goes into `audit_log`: which fields were planned
  and filled, what was left, the form's hash, who confirmed. Values typed from the profile are
  never logged or printed (`‹profile›`), and screenshots, which do show them, stay in
  `~/.trawl/applications` on the person's machine.
- **Limits.** One submission per job, and at most `TRAWL_SUBMIT_DAILY_LIMIT` (5) in 24 hours.

Ashby's form carries an invisible reCAPTCHA Enterprise check, one more reason it runs in the
person's own Chrome and not on a server.

Approval stays in the terminal next to the browser, not in Telegram: the form is approved where it
can be seen, and a phone approval would need a process waiting on the Mac to click Submit. Lever's
form is filled too (section 14).

## 13. The desktop app: one database, two places

Collecting has to run while the laptop is shut, and applying has to run where a person can watch a
browser, so the work is split: the server collects, judges, scores and sends the digest every day;
the Mac tailors the résumé for the jobs the person picks and applies. Tailoring every good fit on
the server sent résumés nobody used.

- **No copy of the data.** The app opens `ssh -L` to the server's Postgres for as long as it runs
  (`TRAWL_SSH_HOST`; BatchMode, so a missing key fails instead of waiting for a password) and closes
  it on exit. There is nothing to sync.
- **A local page in a native window.** FastAPI on 127.0.0.1 and plain HTML/JS, shown in a pywebview
  window; `macos/build.sh` wraps it in `trawl.app` with a small launcher that embeds the checkout's
  Python, so the Dock shows trawl, and a `git pull` takes effect on the next start.
- **Stages record where an application stands.** `stages` holds applied, interview, offer, rejected,
  withdrawn and skipped, from either side: trawl adds "applied" when it submits (that one can't be
  undone from the app, since the submission happened), and the person records what happened
  elsewhere, MyCareersFuture applications included. A job's status is its latest stage, else
  "prepared" with a tailored application, else "new".
- **Jobs, not postings.** As in the digest, a job posted on two boards shows once, and the funnel
  counts fingerprints, so one role on Greenhouse and MyCareersFuture is one job applied to.
- **Preparing and applying are tasks the page follows.** Each runs in its own thread (Playwright's
  sync API stays in the thread that started it) and keeps what it said. Applying runs the same
  `apply.run` as the terminal; its questions ("Type 'submit'", "[y/N]") become prompts with buttons,
  and the task waits for the page's answer. One task per job, and one application at a time: there
  is one Chrome window to watch. A question waiting while another job is open shows in the sidebar.
- **What the page can read is fixed.** Screenshots are served only from that job's folder, by a
  name that can't leave it.

## 14. Lever

Lever's apply page names its fields (no ids): a question's radios, checkboxes or `<select>` share
its name and carry the options as values, so one step kind (`named`) chooses among them. The
location is an autocomplete whose choice lands in a hidden field, so trawl types the city and
picks from the suggestions. The résumé is read as it's attached and may fill other fields, so
trawl waits for the page to settle before typing. Submit is a button behind hCaptcha: when its
puzzle shows, trawl says so and waits for the person, as it does for an emailed code, and the
`/thanks` page is the confirmation. Required fields known only by name, and required selects, count
as empty until filled.

## 15. Where to apply when MyCareersFuture won't take you

MyCareersFuture takes applications only from Singapore citizens and permanent residents, yet most of
the eligible jobs trawl finds are there: employers must advertise a job on it (under the Fair
Consideration Framework) before hiring for it on an Employment Pass or S Pass. So it stays a source
of jobs, and `where.py` looks for the same job somewhere a foreign candidate can apply, cheapest
first:

- **The same job on another board trawl collects** (same fingerprint). A second copy on
  MyCareersFuture itself doesn't count.
- **The company's own board** on Greenhouse, Lever or Ashby, guessed from its name and its website's
  domain. With the job on it, it's a link trawl can fill once the board is collected. A board
  guessed from one word of the name ("dcs") counts only with the job on it, since it may belong to
  someone else.
- **The company's website**, from MyCareersFuture's own company record (by UEN), and its careers
  page if `/careers`, `/jobs` or `/career` answers.
- **A LinkedIn and a Google search**, as links for the person: trawl never scrapes either.

Leads are kept with the posting (`leads`, `leads_at`); `trawl where` looks up the open eligible
MyCareersFuture jobs without them, and the desktop app looks one up on demand. On the first run, 17
of 51 jobs had more than a search, among them a careers page for every large employer, and one
company board trawl didn't collect yet.
