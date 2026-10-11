"""Opt-in real psql regression on an isolated schema in the direct test DB."""

from __future__ import annotations

import json
import os
import uuid
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from gym.ledger import LedgerReader


def test_real_psql_gym_budget():
    dsn = os.environ.get("TOCGATE_TEST_DSN")
    if not dsn:
        pytest.skip("set TOCGATE_TEST_DSN to the direct disposable test database")
    parsed = urlparse(dsn)
    assert parsed.hostname == "127.0.0.1" and parsed.port == 5432
    assert parsed.path == "/agent_os_ledger_test"
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    schema = "tocgate_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            conn.execute("""CREATE TABLE launch_requests (launch_request_id text, worker_class text,
                           request_payload_json text);
                CREATE TABLE worker_runs (run_id text, launch_request_id text, status text,
                  project_ref text, started_at timestamptz, metadata_json text,
                  token_usage_source text, token_usage_input bigint, token_usage_cache_read bigint,
                  token_usage_cache_write bigint, token_usage_output bigint,
                  token_usage_cost_usd numeric, turn_count bigint);
                CREATE TABLE consumption_events (event_source text, source_row_id text,
                  input_fresh bigint, cache_read bigint, cache_write bigint, output bigint);
                INSERT INTO launch_requests VALUES ('lrq', 'codex', '{"workerSpec":{"model":"gpt-6-sol"}}');
                INSERT INTO worker_runs VALUES ('run', 'lrq', 'succeeded', 'adversarial-eval-gym',
                  '2026-09-30', '{}', 'codex-exec-json', 10, 3, 0, 2, 0.1, 1);
                INSERT INTO consumption_events VALUES ('worker_runs', 'run', 10, 3, 0, 2);""")
            target = SimpleNamespace(
                backend="postgres", dsn=make_conninfo(dsn, options=f"-csearch_path={schema}")
            )
            reader = LedgerReader(None, target_resolver=lambda: target)
            conn.execute("""
                ALTER TABLE worker_runs ADD COLUMN session_id text;
                ALTER TABLE worker_runs ADD COLUMN phase text;
                ALTER TABLE worker_runs ADD COLUMN worktree_path text;
                ALTER TABLE worker_runs ADD COLUMN failure_class text;
                ALTER TABLE worker_runs ADD COLUMN failure_detail text;
                CREATE TABLE transcript_entries (runtime_session_id text, ordinal int,
                    event_type text, event_role text, tool_metadata_json text, content_json text);
                CREATE TABLE control_evaluations (runtime_session_id text, step_name text,
                    step_type text, enforcement_action text, evaluation_status text, observed_at text);
            """)
            assert reader.worker_run_for_launch("lrq")["status"] == "succeeded"
            assert reader.worker_run_for_launch("missing") is None
            assert reader.transcript_rows("session") == []
            assert reader.control_rows("session") == []
            assert reader.worker_class_for_launch("lrq") == "codex"
            assert reader.worker_class_for_launch("unknown") is None
            samples = reader.gym_token_samples("codex", "2026-09-01")
            assert samples == [
                {
                    "model": "gpt-6-sol",
                    "inputTokens": 13,
                    "cacheReadTokens": 3,
                    "outputTokens": 2,
                    "costUsd": 0.1,
                    "turns": 1,
                }
            ]
            assert reader.gym_token_samples("codex' OR true --", "2026-09-01") == []
            from cwp_dispatch import tool_output_compression_arm as toc_arm

            for arm in toc_arm.LAUNCH_ARMS:
                args = SimpleNamespace(tool_output_compression=arm)
                spec = toc_arm.apply_launch_arm(
                    {}, toc_arm.launch_arm_from_args(args, toc_arm.GYM_PROJECT)
                )
                receipt = toc_arm.worker_run_metadata(spec)
                conn.execute("UPDATE worker_runs SET metadata_json = %s", (json.dumps(receipt),))
                assert reader.compression_arm_for_launch("lrq") == {
                    "requested": arm,
                    "recorded": [arm],
                    "effective": [None],
                }
                receipt["toolOutputCompressionEffective"] = {"arm": arm, "source": "adapter"}
                conn.execute("UPDATE worker_runs SET metadata_json = %s", (json.dumps(receipt),))
                assert reader.compression_arm_for_launch("lrq")["effective"] == [arm]

        finally:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
