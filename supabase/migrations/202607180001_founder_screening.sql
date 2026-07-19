begin;

create table if not exists public.founders (
  candidate_id text primary key,
  full_name text not null,
  given_name text,
  family_name text,
  aliases jsonb not null default '[]'::jsonb,
  headline text,
  current_founder_role text,
  affiliations jsonb not null default '[]'::jsonb,
  location text,
  geography text,
  company_name text,
  company_url text,
  founder_role text,
  founder_relationship_evidence_url text,
  profile_urls jsonb not null default '[]'::jsonb,
  discovery_sources jsonb not null default '[]'::jsonb,
  entrepreneurial_signals jsonb not null default '[]'::jsonb,
  research jsonb not null default '{}'::jsonb,
  hackathons jsonb not null default '{}'::jsonb,
  open_source jsonb not null default '{}'::jsonb,
  founder_features jsonb not null default '{}'::jsonb,
  missing_fields jsonb not null default '[]'::jsonb,
  contradicted_fields jsonb not null default '[]'::jsonb,
  has_bachelor boolean,
  has_master boolean,
  has_phd boolean,
  highest_documented_level text,
  highest_ranked_institution text,
  best_qs_world_rank text,
  qs_edition integer,
  verified_prior_exit_count integer check (verified_prior_exit_count is null or verified_prior_exit_count >= 0),
  reported_prior_exit_count integer check (reported_prior_exit_count is null or reported_prior_exit_count >= 0),
  documented_skill_count integer not null default 0 check (documented_skill_count >= 0),
  first_seen_at timestamptz,
  last_seen_at timestamptz,
  raw_record jsonb not null,
  imported_at timestamptz not null default now()
);

create table if not exists public.founder_companies (
  company_id text primary key,
  company_name text not null,
  company_url text,
  company_profile_url text,
  status text,
  batch text,
  industry text,
  subindustry text,
  launched_at date,
  raw_record jsonb not null,
  imported_at timestamptz not null default now()
);

create table if not exists public.founder_company_roles (
  candidate_id text not null references public.founders(candidate_id) on delete cascade,
  company_id text not null references public.founder_companies(company_id) on delete cascade,
  founder_role text,
  relationship_evidence_url text,
  is_primary boolean not null default false,
  raw_record jsonb not null,
  primary key (candidate_id, company_id)
);

create table if not exists public.founder_education (
  education_id text primary key,
  candidate_id text not null references public.founders(candidate_id) on delete cascade,
  level text,
  degree_name text,
  institution text,
  status text,
  completed boolean,
  qs_world_rank text,
  qs_edition integer,
  education_source_url text,
  qs_source_url text,
  evidence_excerpt text,
  raw_record jsonb not null
);

create table if not exists public.founder_skills (
  founder_skill_id text primary key,
  candidate_id text not null references public.founders(candidate_id) on delete cascade,
  skill_name text not null,
  evidence_type text,
  evidence_url text,
  evidence_excerpt text,
  raw_record jsonb not null
);

create table if not exists public.founder_exits (
  exit_id text primary key,
  candidate_id text not null references public.founders(candidate_id) on delete cascade,
  company_name text,
  outcome text not null,
  source_status text,
  evidence_url text,
  evidence_excerpt text,
  raw_record jsonb not null
);

create table if not exists public.founder_reported_exit_claims (
  claim_id text primary key,
  candidate_id text not null references public.founders(candidate_id) on delete cascade,
  reported_count integer not null check (reported_count >= 0),
  source_url text,
  evidence_excerpt text,
  verification_status text not null default 'founder_reported',
  raw_record jsonb not null
);

create table if not exists public.founder_evidence (
  source_id text primary key,
  candidate_id text not null references public.founders(candidate_id) on delete cascade,
  source_type text,
  document_name text,
  source_uri text,
  document_date date,
  collected_at timestamptz,
  location text,
  evidence_excerpt text,
  verification_status text,
  confidence text,
  raw_metadata jsonb not null default '{}'::jsonb,
  raw_record jsonb not null
);

create index if not exists founders_full_name_idx on public.founders (lower(full_name));
create index if not exists founders_company_name_idx on public.founders (lower(company_name));
create index if not exists founders_geography_idx on public.founders (geography);
create index if not exists founders_qs_rank_idx on public.founders (best_qs_world_rank);
create index if not exists founder_company_roles_company_idx on public.founder_company_roles (company_id);
create index if not exists founder_education_candidate_idx on public.founder_education (candidate_id);
create index if not exists founder_education_institution_idx on public.founder_education (lower(institution));
create index if not exists founder_skills_candidate_idx on public.founder_skills (candidate_id);
create index if not exists founder_skills_name_idx on public.founder_skills (lower(skill_name));
create index if not exists founder_exits_candidate_idx on public.founder_exits (candidate_id);
create index if not exists founder_evidence_candidate_idx on public.founder_evidence (candidate_id);
create index if not exists founder_evidence_source_type_idx on public.founder_evidence (source_type);

alter table public.founders enable row level security;
alter table public.founder_companies enable row level security;
alter table public.founder_company_roles enable row level security;
alter table public.founder_education enable row level security;
alter table public.founder_skills enable row level security;
alter table public.founder_exits enable row level security;
alter table public.founder_reported_exit_claims enable row level security;
alter table public.founder_evidence enable row level security;

revoke all on public.founders from anon, authenticated;
revoke all on public.founder_companies from anon, authenticated;
revoke all on public.founder_company_roles from anon, authenticated;
revoke all on public.founder_education from anon, authenticated;
revoke all on public.founder_skills from anon, authenticated;
revoke all on public.founder_exits from anon, authenticated;
revoke all on public.founder_reported_exit_claims from anon, authenticated;
revoke all on public.founder_evidence from anon, authenticated;

grant all on public.founders to service_role;
grant all on public.founder_companies to service_role;
grant all on public.founder_company_roles to service_role;
grant all on public.founder_education to service_role;
grant all on public.founder_skills to service_role;
grant all on public.founder_exits to service_role;
grant all on public.founder_reported_exit_claims to service_role;
grant all on public.founder_evidence to service_role;

commit;
