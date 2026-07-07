"""Tests for audit log — log_operation, get_recent_operations."""

import json
import tempfile
from pathlib import Path

import agents.audit as audit_module


class TestAuditLog:
    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.orig_audit_file = audit_module.AUDIT_FILE
        audit_module.AUDIT_FILE = Path(self.tmp_dir) / "audit.jsonl"

    def teardown_method(self):
        audit_module.AUDIT_FILE = self.orig_audit_file

    def test_log_operation_writes_jsonl(self):
        audit_module.log_operation("bash", "pytest tests/", user="agent", success=True)
        assert audit_module.AUDIT_FILE.exists()
        with open(audit_module.AUDIT_FILE) as f:
            lines = f.readlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["operation"] == "bash"
        assert entry["user"] == "agent"
        assert entry["success"] is True

    def test_get_recent_operations_reads_back(self):
        audit_module.log_operation("op1")
        audit_module.log_operation("op2")
        ops = audit_module.get_recent_operations(limit=10)
        assert len(ops) == 2
        assert ops[0]["operation"] == "op1"
        assert ops[1]["operation"] == "op2"

    def test_get_recent_operations_limit(self):
        for i in range(5):
            audit_module.log_operation(f"op{i}")
        ops = audit_module.get_recent_operations(limit=2)
        assert len(ops) == 2
        assert ops[0]["operation"] == "op3"
        assert ops[1]["operation"] == "op4"

    def test_get_recent_operations_handles_corrupt_line(self):
        audit_module.log_operation("ok")
        with open(audit_module.AUDIT_FILE, "a") as f:
            f.write("not valid json\n")
        audit_module.log_operation("after")
        ops = audit_module.get_recent_operations(limit=10)
        assert len(ops) == 2

    def test_get_recent_operations_missing_file_returns_empty(self):
        ops = audit_module.get_recent_operations()
        assert ops == []
