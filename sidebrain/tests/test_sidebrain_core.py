import json
from pathlib import Path

from sidebrain.cli import build_parser
from sidebrain.mcp.server import _extract_body, _extract_frontmatter


def test_writer_skips_exact_duplicate_and_sanitizes_filename(tmp_path, monkeypatch):
    import sidebrain.process.dedup as dedup
    import sidebrain.process.writer as writer

    processed = tmp_path / "knowledge" / "processed"
    quarantine = tmp_path / "knowledge" / "quarantine"
    monkeypatch.setattr(writer, "PROCESSED", processed)
    monkeypatch.setattr(writer, "QUARANTINE", quarantine)
    monkeypatch.setattr(dedup, "PROCESSED", processed)

    extracted = {
        "summary": "Windows path source",
        "key_points": ["same content"],
        "tags": ["sidebrain"],
    }

    first = writer.write_processed(
        extracted,
        source="test",
        source_path="C:\\Users\\Administrator\\source.md",
        source_id="C:\\Users\\Administrator\\source.md",
    )
    second = writer.write_processed(
        extracted,
        source="test",
        source_path="C:\\Users\\Administrator\\source.md",
        source_id="C:\\Users\\Administrator\\source-copy.md",
    )

    files = list(processed.rglob("*.md"))
    assert first["success"] is True
    assert second["success"] is True
    assert second["duplicate"] is True
    assert len(files) == 1
    assert ":" not in files[0].name
    assert "\\" not in files[0].name


def test_process_dry_run_recurses_raw_and_respects_batch(tmp_path, monkeypatch):
    import sidebrain.process_pipeline as pipeline

    raw = tmp_path / "knowledge" / "raw"
    raw_pi = raw / "pi"
    raw_meetings = raw / "meetings"
    raw_ad_hoc = raw / "ad_hoc"
    raw_ga = raw / "ga"
    state = tmp_path / "state"
    for directory in (raw_pi / "host-a", raw_meetings, raw_ad_hoc, raw_ga, state):
        directory.mkdir(parents=True, exist_ok=True)

    (raw_pi / "host-a" / "one.md").write_text("one", encoding="utf-8")
    (raw_pi / "two.md").write_text("two", encoding="utf-8")
    (raw_ad_hoc / "three.md").write_text("three", encoding="utf-8")

    monkeypatch.setattr(pipeline, "RAW_PI", raw_pi)
    monkeypatch.setattr(pipeline, "RAW_MEETINGS", raw_meetings)
    monkeypatch.setattr(pipeline, "RAW_AD_HOC", raw_ad_hoc)
    monkeypatch.setattr(pipeline, "RAW_GA", raw_ga)
    monkeypatch.setattr(pipeline, "CURSOR_FILE", state / "process_cursor.json")
    monkeypatch.setattr(pipeline, "load_config", lambda: {"process": {"batch_size": 2}})

    result = pipeline.process_all(dry_run=True)

    assert result["total"] == 3
    assert result["dispatched"] == 2
    assert result["deferred"] == 1
    assert result["errors"] == 0


def test_process_dry_run_batch_size_override(tmp_path, monkeypatch):
    import sidebrain.process_pipeline as pipeline

    raw = tmp_path / "knowledge" / "raw"
    raw_pi = raw / "pi"
    raw_meetings = raw / "meetings"
    raw_ad_hoc = raw / "ad_hoc"
    raw_ga = raw / "ga"
    state = tmp_path / "state"
    for directory in (raw_pi, raw_meetings, raw_ad_hoc, raw_ga, state):
        directory.mkdir(parents=True, exist_ok=True)

    (raw_pi / "one.md").write_text("one", encoding="utf-8")
    (raw_pi / "two.md").write_text("two", encoding="utf-8")

    monkeypatch.setattr(pipeline, "RAW_PI", raw_pi)
    monkeypatch.setattr(pipeline, "RAW_MEETINGS", raw_meetings)
    monkeypatch.setattr(pipeline, "RAW_AD_HOC", raw_ad_hoc)
    monkeypatch.setattr(pipeline, "RAW_GA", raw_ga)
    monkeypatch.setattr(pipeline, "CURSOR_FILE", state / "process_cursor.json")
    monkeypatch.setattr(pipeline, "load_config", lambda: {"process": {"batch_size": 10}})

    result = pipeline.process_all(dry_run=True, batch_size=1)

    assert result["total"] == 2
    assert result["dispatched"] == 1
    assert result["deferred"] == 1


def test_mcp_parser_supports_json_and_yamlish_frontmatter():
    json_body = {"summary": "json memory", "key_points": ["a"]}
    json_text = (
        "---"
        + json.dumps({"id": "abc", "source": "test", "tags": ["x"]})
        + "---\n\n```json\n"
        + json.dumps(json_body)
        + "\n```"
    )
    yaml_text = "---\ncreated: 2026-06-04\ntags: [x, y]\n---\n\n# yaml memory\n\n- point one\n"

    assert _extract_frontmatter(json_text)["id"] == "abc"
    assert _extract_body(json_text)["summary"] == "json memory"

    yaml_frontmatter = _extract_frontmatter(yaml_text)
    yaml_body = _extract_body(yaml_text)
    assert yaml_frontmatter["schema_version"] == 1
    assert yaml_frontmatter["tags"] == ["x", "y"]
    assert yaml_body["summary"] == "yaml memory"
    assert yaml_body["key_points"] == ["point one"]


def test_doctor_has_opt_in_llm_flag():
    parser = build_parser()

    default_args = parser.parse_args(["doctor"])
    llm_args = parser.parse_args(["doctor", "--llm"])

    assert default_args.llm is False
    assert llm_args.llm is True


def test_daemon_run_once_does_not_sync_to_pi(monkeypatch):
    import sidebrain.daemon as daemon

    calls = []
    cfg = {
        "ingest": {
            "pi": {"enabled": True, "batch_size": 10, "max_size_mb": 50},
            "meetings": {"enabled": True, "batch_size": 10},
        },
        "sync": {"tags_filter": ["rule"], "max_items": 50},
    }

    monkeypatch.setattr(daemon, "ingest_pi_sessions", lambda **kwargs: calls.append("pi") or {"ingested": 0})
    monkeypatch.setattr(daemon, "ingest_meetings", lambda **kwargs: calls.append("meetings") or {"ingested": 0})
    monkeypatch.setattr(daemon, "ingest_ga_sessions", lambda: calls.append("ga") or {"ingested": 0})
    monkeypatch.setattr(daemon, "process_all", lambda: calls.append("process") or {"dispatched": 0})

    result = daemon._run_once(cfg)

    assert calls == ["pi", "meetings", "ga", "process"]
    assert "sync" not in result


def test_lifecycle_metadata_and_resolve_filtering(tmp_path, monkeypatch):
    import sidebrain.mcp.server as mcp_server
    import sidebrain.process.dedup as dedup
    import sidebrain.process.writer as writer

    processed = tmp_path / "knowledge" / "processed"
    quarantine = tmp_path / "knowledge" / "quarantine"
    monkeypatch.setattr(writer, "PROCESSED", processed)
    monkeypatch.setattr(writer, "QUARANTINE", quarantine)
    monkeypatch.setattr(dedup, "PROCESSED", processed)
    monkeypatch.setattr(mcp_server, "PROCESSED", processed)

    result = writer.write_processed(
        {
            "summary": "Lifecycle memory",
            "key_points": ["active point"],
            "tags": ["life"],
            "topic_key": "architecture/lifecycle",
            "type": "decision",
        },
        source="test",
        source_path="/tmp/source.md",
        source_id="source-1",
    )
    assert result["success"] is True

    before = mcp_server.tool_sidebrain_search("Lifecycle", limit=5)
    assert len(before["entries"]) == 1
    assert before["entries"][0]["topic_key"] == "architecture/lifecycle"
    assert before["entries"][0]["status"] == "active"
    assert before["entries"][0]["type"] == "decision"

    resolved = mcp_server.tool_sidebrain_resolve("architecture/lifecycle", reason="done")
    assert resolved["updated"] is True
    assert resolved["status"] == "resolved"

    after_default = mcp_server.tool_sidebrain_search("Lifecycle", limit=5)
    after_all = mcp_server.tool_sidebrain_search("Lifecycle", limit=5, status="all")
    assert after_default["entries"] == []
    assert len(after_all["entries"]) == 1
    assert after_all["entries"][0]["status"] == "resolved"


def test_writer_upserts_active_topic_key(tmp_path, monkeypatch):
    import sidebrain.process.dedup as dedup
    import sidebrain.process.writer as writer

    processed = tmp_path / "knowledge" / "processed"
    quarantine = tmp_path / "knowledge" / "quarantine"
    monkeypatch.setattr(writer, "PROCESSED", processed)
    monkeypatch.setattr(writer, "QUARANTINE", quarantine)
    monkeypatch.setattr(dedup, "PROCESSED", processed)

    first = writer.write_processed(
        {
            "summary": "Topic first",
            "key_points": ["old"],
            "tags": ["topic"],
            "topic_key": "architecture/topic-upsert",
            "type": "decision",
        },
        source="test",
        source_path="/tmp/one.md",
        source_id="one",
    )
    second = writer.write_processed(
        {
            "summary": "Topic second",
            "key_points": ["new"],
            "tags": ["topic"],
            "topic_key": "architecture/topic-upsert",
            "type": "decision",
        },
        source="test",
        source_path="/tmp/two.md",
        source_id="two",
    )

    files = list(processed.rglob("*.md"))
    text = files[0].read_text(encoding="utf-8")
    assert len(files) == 1
    assert second["updated"] is True
    assert second["id"] == first["id"]
    assert "Topic second" in text
    assert "old" not in text


def test_mcp_structured_ingest_upserts_topic_key(tmp_path, monkeypatch):
    import sidebrain.mcp.server as mcp_server
    import sidebrain.process.dedup as dedup
    import sidebrain.process.writer as writer

    processed = tmp_path / "knowledge" / "processed"
    quarantine = tmp_path / "knowledge" / "quarantine"
    monkeypatch.setattr(writer, "PROCESSED", processed)
    monkeypatch.setattr(writer, "QUARANTINE", quarantine)
    monkeypatch.setattr(dedup, "PROCESSED", processed)
    monkeypatch.setattr(mcp_server, "PROCESSED", processed)

    first = mcp_server.tool_sidebrain_ingest(
        summary="MCP topic first",
        key_points=["old"],
        tags=["mcp"],
        topic_key="mcp/topic-upsert",
        type="decision",
    )
    second = mcp_server.tool_sidebrain_ingest(
        summary="MCP topic second",
        key_points=["new"],
        tags=["mcp"],
        topic_key="mcp/topic-upsert",
        type="decision",
    )

    entries = mcp_server.tool_sidebrain_search("MCP topic", status="all")["entries"]
    detail = mcp_server.tool_sidebrain_get("mcp/topic-upsert")["entry"]
    assert first["ingested"] == 1
    assert second["updated"] is True
    assert len(list(processed.rglob("*.md"))) == 1
    assert len(entries) == 1
    assert detail["summary"] == "MCP topic second"
    assert detail["key_points"] == ["new"]


def test_validate_extraction_normalizes_legacy_shapes():
    from sidebrain.process.validate import normalize_extraction

    normalized, errors = normalize_extraction(
        {
            "summary": "  Sidebrain schema validation  ",
            "key_points": "single point",
            "decisions": ["Use validator"],
            "tags": ["sidebrain", ""],
            "confidence": 0.75,
        }
    )

    assert errors == []
    assert normalized is not None
    assert normalized["summary"] == "Sidebrain schema validation"
    assert normalized["key_points"] == ["single point"]
    assert normalized["decisions"] == [{"decision": "Use validator"}]
    assert normalized["tags"] == ["sidebrain"]
    assert normalized["confidence"] == 0.75


def test_validate_extraction_rejects_missing_summary_and_bad_status():
    from sidebrain.process.validate import normalize_extraction

    normalized, errors = normalize_extraction(
        {
            "summary": " ",
            "status": "pending",
            "confidence": 2,
            "decisions": [{"reasoning": "missing decision"}],
        }
    )

    assert normalized is None
    assert "summary is required" in errors
    assert any("status must be one of" in error for error in errors)
    assert "confidence must be between 0 and 1" in errors
    assert "decisions[0].decision is required" in errors


def test_process_pipeline_quarantines_invalid_extraction(tmp_path, monkeypatch):
    import sidebrain.process.validate as validate
    import sidebrain.process_pipeline as pipeline

    raw = tmp_path / "knowledge" / "raw"
    raw_pi = raw / "pi"
    raw_meetings = raw / "meetings"
    raw_ad_hoc = raw / "ad_hoc"
    raw_ga = raw / "ga"
    state = tmp_path / "state"
    quarantine = tmp_path / "knowledge" / "quarantine"
    for directory in (raw_pi, raw_meetings, raw_ad_hoc, raw_ga, state, quarantine):
        directory.mkdir(parents=True, exist_ok=True)

    source = raw_ad_hoc / "bad.md"
    source.write_text("bad source", encoding="utf-8")
    output_path = tmp_path / "ga" / "temp" / "_bad_extracted.json"

    def fake_call_ga(task_name, prompt):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({"tags": ["bad"]}), encoding="utf-8")
        return {"success": True, "output": "", "stderr": "", "returncode": 0}

    monkeypatch.setattr(pipeline, "RAW_PI", raw_pi)
    monkeypatch.setattr(pipeline, "RAW_MEETINGS", raw_meetings)
    monkeypatch.setattr(pipeline, "RAW_AD_HOC", raw_ad_hoc)
    monkeypatch.setattr(pipeline, "RAW_GA", raw_ga)
    monkeypatch.setattr(pipeline, "STATE", state)
    monkeypatch.setattr(pipeline, "CURSOR_FILE", state / "process_cursor.json")
    monkeypatch.setattr(pipeline, "GA_ROOT", tmp_path / "ga")
    monkeypatch.setattr(pipeline, "_call_ga", fake_call_ga)
    monkeypatch.setattr(pipeline, "load_config", lambda: {"process": {"batch_size": 10}})
    monkeypatch.setattr(validate, "QUARANTINE", quarantine)

    result = pipeline.process_all(dry_run=False)

    quarantine_files = list(quarantine.glob("validation__bad__*.json"))
    cursor = json.loads((state / "process_cursor.json").read_text(encoding="utf-8"))
    assert result["errors"] == 1
    assert result["dispatched"] == 0
    assert len(quarantine_files) == 1
    assert cursor["processed_hashes"] == []
    metrics = json.loads((state / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["last_process_total"] == 1
    assert metrics["last_process_errors"] == 1


def test_search_ranks_topic_key_and_filters_type(tmp_path, monkeypatch):
    import sidebrain.mcp.server as mcp_server
    import sidebrain.process.dedup as dedup
    import sidebrain.process.writer as writer

    processed = tmp_path / "knowledge" / "processed"
    quarantine = tmp_path / "knowledge" / "quarantine"
    monkeypatch.setattr(writer, "PROCESSED", processed)
    monkeypatch.setattr(writer, "QUARANTINE", quarantine)
    monkeypatch.setattr(dedup, "PROCESSED", processed)
    monkeypatch.setattr(mcp_server, "PROCESSED", processed)

    writer.write_processed(
        {
            "summary": "Contains architecture search-rank words",
            "key_points": ["plain summary match"],
            "tags": ["search"],
            "type": "note",
        },
        source="test",
        source_path="/tmp/plain.md",
        source_id="plain",
    )
    writer.write_processed(
        {
            "summary": "Topic keyed memory",
            "key_points": ["specific topic"],
            "tags": ["rank"],
            "topic_key": "architecture/search-rank",
            "type": "decision",
        },
        source="test",
        source_path="/tmp/topic.md",
        source_id="topic",
    )

    results = mcp_server.tool_sidebrain_search("search-rank", limit=5)["entries"]
    decision_results = mcp_server.tool_sidebrain_search("", limit=5, type="decision")["entries"]

    assert results[0]["topic_key"] == "architecture/search-rank"
    assert results[0]["score"] > results[1]["score"]
    assert len(decision_results) == 1
    assert decision_results[0]["type"] == "decision"


def test_get_expands_related_and_supersedes(tmp_path, monkeypatch):
    import sidebrain.mcp.server as mcp_server
    import sidebrain.process.dedup as dedup
    import sidebrain.process.writer as writer

    processed = tmp_path / "knowledge" / "processed"
    quarantine = tmp_path / "knowledge" / "quarantine"
    monkeypatch.setattr(writer, "PROCESSED", processed)
    monkeypatch.setattr(writer, "QUARANTINE", quarantine)
    monkeypatch.setattr(dedup, "PROCESSED", processed)
    monkeypatch.setattr(mcp_server, "PROCESSED", processed)

    writer.write_processed(
        {
            "summary": "Related base",
            "key_points": ["base"],
            "topic_key": "memory/base",
            "type": "note",
        },
        source="test",
        source_path="/tmp/base.md",
        source_id="base",
    )
    writer.write_processed(
        {
            "summary": "Old decision",
            "key_points": ["old"],
            "topic_key": "memory/old",
            "type": "decision",
        },
        source="test",
        source_path="/tmp/old.md",
        source_id="old",
    )
    writer.write_processed(
        {
            "summary": "Current decision",
            "key_points": ["current"],
            "topic_key": "memory/current",
            "type": "decision",
            "related": ["memory/base"],
            "supersedes": ["memory/old"],
        },
        source="test",
        source_path="/tmp/current.md",
        source_id="current",
    )

    detail = mcp_server.tool_sidebrain_get("memory/current")["entry"]

    assert detail["related_entries"][0]["topic_key"] == "memory/base"
    assert detail["supersedes_entries"][0]["topic_key"] == "memory/old"
    assert detail["related_entries"][0]["summary"] == "Related base"
