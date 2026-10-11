#!/usr/bin/env bash
# Stage the redacted FK-drain incident bundle. Source doc:
# docs/SPEC-session-ledger-fk-recovery-2026-06-04.md
#
# Only ever writes inputs the agent may see. The bundle carries ALL evidence ids
# (correct + red-herring) so a wrong theory is plausibly supportable; it never
# reveals WHICH evidence id / root cause is the answer — that lives only in
# expected/ and in verifier.py, out of band.
set -euo pipefail

sandbox_root="${1:-.}"
mkdir -p "${sandbox_root}/incident"

cat >"${sandbox_root}/incident/daemon-errors.log" <<'LOG'
# Redacted dispatch-daemon error log excerpt (host paths scrubbed).

evidence-id: ev-701  04:01:07  daemon(pid=redacted) cpu=98.2% sustained; admit
  queue starved: 19 failed / 4 succeeded since 00:00Z (79% LRQ failure rate);
  5 reaped-stuck-requested entries.

evidence-id: ev-702  04:01:22  dual_write.replay backlog: 7352 pending replays,
  1451 dead-lettered under session-ledger/dual-write-replay/; each retrying every
  ~30s.

evidence-id: ev-703  04:01:22  ledger.dual_write.replay_failed
  IntegrityError: insert or update on table "transcript_sources" violates foreign
  key constraint "fk_transcript_sources_source_runtime_session_uuid"
  DETAIL: Key (source_runtime_session_uuid)=(019e9164-c8c4-7201-949a-bccdbe0a39dd)
  is not present in table "runtime_sessions".
  note: 019e9164-... is a codex adapter_runtime_id (upstream session), NOT any
  runtime_sessions.session_uuid. The corresponding SQLite runtime_sessions row
  exists as ses_b80119d8-... -> session_uuid b80119d8-..., a different value.
  Same pattern for acpx (record ids) and chat_archive (string schemes).

evidence-id: ev-706  04:02:10  same IntegrityError class repeating for six
  distinct codex session UUIDv7 source values; all resolve to recent codex
  sessions whose runtime_sessions rows carry unrelated session_uuid values.
LOG

cat >"${sandbox_root}/incident/schema-and-adapters.md" <<'MD'
# Redacted schema + adapter notes (from the on-call investigation)

## The constraint added by migration 095 (uuid_shadow_graph)

    FK fk_transcript_sources_source_runtime_session_uuid:
      transcript_sources.source_runtime_session_uuid
        -> runtime_sessions.session_uuid  (uuid NOT NULL)  ON DELETE SET NULL, NOT VALID

The same shape exists for transcript_entries.source_runtime_session_uuid.

## What the source column actually holds (per adapter)

evidence-id: ev-704  source_runtime_session_id is polymorphic:
  - codex        -> the codex session UUIDv7 (== runtime_sessions.adapter_runtime_id)
  - acpx         -> an internal acpx record id
  - chat_archive -> a string scheme "chat_archive:<stable_key>"
  None of these consistently equals any runtime_sessions.session_uuid.

## Investigation: is retargeting the FK to adapter_runtime_id viable?

evidence-id: ev-705  adapter_runtime_id is NOT unique and cannot be made unique:
  883 values appear in 2 distinct runtime_sessions rows; 4 in 3 rows; one value
  in 235 rows (a clio openclaw session compacted 235 times in a day — each
  archive is a meaningful row). adapter_runtime_id is a *family* identifier, so
  deduping to enforce uniqueness would destroy archival history. Retargeting is
  a dead end; the recovery dropped the FK instead.
MD
