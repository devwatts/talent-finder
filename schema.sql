-- Everything a search produces is kept, so a ranking can be explained and reproduced later.
--   psql "$SUPABASE_DB_URL" -f schema.sql
create schema if not exists app;

create table if not exists app.searches (
  id         bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  jd_text    text  not null,
  jd         jsonb not null,                     -- the parsed job description
  company    jsonb not null,                     -- what we know about the hiring company
  status     text  not null default 'draft',     -- draft, running, done or failed
  progress   text
);

create table if not exists app.rubrics (
  search_id  bigint not null references app.searches on delete cascade,
  version    int    not null,
  rubric     jsonb  not null,
  frozen     boolean not null default false,     -- set on the version a full run was scored with
  created_at timestamptz not null default now(),
  primary key (search_id, version)
);

create table if not exists app.profiles (
  id         text primary key,                   -- LinkedIn profile id
  data       jsonb not null,                     -- the trimmed profile
  fetched_at timestamptz not null default now()
);

create table if not exists app.scores (
  search_id      bigint not null references app.searches on delete cascade,
  rubric_version int    not null,
  profile_id     text   not null references app.profiles,
  stage          text   not null,                -- probe or full
  assessment     jsonb  not null,                -- the facts read off the profile
  score          real   not null,
  primary key (search_id, rubric_version, profile_id)
);

create table if not exists app.calibrations (
  search_id  bigint  not null references app.searches on delete cascade,
  profile_id text    not null references app.profiles,
  good_fit   boolean not null,
  note       text,
  created_at timestamptz not null default now(),
  primary key (search_id, profile_id)
);

create table if not exists app.employers (
  name text primary key,                         -- lower-cased company name
  data jsonb not null                            -- what kind of employer it is
);

comment on table app.searches     is 'One row per job description being sourced for.';
comment on table app.rubrics      is 'Every version of a search''s rubric. Calibration adds a version; a full run freezes one.';
comment on table app.profiles     is 'LinkedIn profiles fetched through Apify, trimmed to what a recruiter reads.';
comment on table app.scores       is 'The facts read off a profile under one rubric version, and the score computed from them.';
comment on table app.calibrations is 'The recruiter''s yes or no on probe profiles.';
comment on table app.employers    is 'Company memory: each employer is classified once and reused for every candidate.';
