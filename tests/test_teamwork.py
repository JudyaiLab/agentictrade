"""Tests for Teamwork package — agent config, task routing, quality gates, orchestrator."""
from __future__ import annotations

import pytest
import tempfile
from pathlib import Path

from marketplace.db import Database
from teamwork.agent_config import (
    AgentProfile, TeamConfig,
    validate_agent_profile, validate_team_config,
)
from teamwork.task_router import TaskRouter, RoutingRule
from teamwork.quality_gates import QualityGate, QualityPipeline
from teamwork.orchestrator import TeamOrchestrator, OrchestratorError
from teamwork.templates import (
    get_team_template, get_service_template,
    list_team_templates, list_service_templates,
)


# ============================================================
# Agent Config Tests
# ============================================================

class TestAgentProfile:
    def test_matches_skill(self):
        agent = AgentProfile(agent_id="a1", skills=("python", "API"))
        assert agent.matches_skill("python") is True
        assert agent.matches_skill("PYTHON") is True  # case insensitive
        assert agent.matches_skill("java") is False

    def test_matches_any_skill(self):
        agent = AgentProfile(agent_id="a1", skills=("python", "ml"))
        assert agent.matches_any_skill(["python", "java"]) is True
        assert agent.matches_any_skill(["java", "go"]) is False

    def test_default_values(self):
        agent = AgentProfile()
        assert agent.role == "worker"
        assert agent.max_concurrent_tasks == 5
        assert agent.timeout_seconds == 300
        assert agent.retry_limit == 3


class TestValidateProfile:
    def test_valid_profile(self):
        profile = AgentProfile(agent_id="a1", role="worker")
        assert validate_agent_profile(profile) == []

    def test_missing_id(self):
        profile = AgentProfile(agent_id="", role="worker")
        errors = validate_agent_profile(profile)
        assert any("agent_id" in e for e in errors)

    def test_invalid_role(self):
        profile = AgentProfile(agent_id="a1", role="invalid")
        errors = validate_agent_profile(profile)
        assert any("role" in e for e in errors)

    def test_invalid_concurrent(self):
        profile = AgentProfile(agent_id="a1", max_concurrent_tasks=0)
        errors = validate_agent_profile(profile)
        assert any("concurrent" in e for e in errors)


class TestTeamConfig:
    def test_valid_config(self):
        config = TeamConfig(name="Team1")
        assert validate_team_config(config) == []

    def test_missing_name(self):
        config = TeamConfig(name="")
        errors = validate_team_config(config)
        assert any("name" in e for e in errors)

    def test_invalid_threshold(self):
        config = TeamConfig(name="T", quality_threshold=11.0)
        errors = validate_team_config(config)
        assert any("threshold" in e for e in errors)

    def test_invalid_routing_mode(self):
        config = TeamConfig(name="T", routing_mode="invalid")
        errors = validate_team_config(config)
        assert any("routing_mode" in e for e in errors)


# ============================================================
# Task Router Tests
# ============================================================

class TestKeywordRouting:
    def test_keyword_match(self):
        router = TaskRouter(routing_mode="keyword")
        rules = [
            {"id": "r1", "keywords": ["debug", "fix"], "target_agent_id": "agent-dev", "priority": 10, "enabled": True},
            {"id": "r2", "keywords": ["write", "blog"], "target_agent_id": "agent-writer", "priority": 5, "enabled": True},
        ]
        result = router.route("Please fix the login bug", rules, [])
        assert result is not None
        assert result.agent_id == "agent-dev"
        assert result.matched_keyword == "fix"

    def test_no_keyword_match(self):
        router = TaskRouter(routing_mode="keyword")
        rules = [
            {"id": "r1", "keywords": ["deploy"], "target_agent_id": "agent-ops", "priority": 10, "enabled": True},
        ]
        result = router.route("Write a blog post", rules, [])
        assert result is None

    def test_priority_ordering(self):
        router = TaskRouter(routing_mode="keyword")
        rules = [
            {"id": "r1", "keywords": ["code"], "target_agent_id": "low-pri", "priority": 1, "enabled": True},
            {"id": "r2", "keywords": ["code"], "target_agent_id": "high-pri", "priority": 10, "enabled": True},
        ]
        result = router.route("Write some code", rules, [])
        assert result.agent_id == "high-pri"

    def test_disabled_rule_skipped(self):
        router = TaskRouter(routing_mode="keyword")
        rules = [
            {"id": "r1", "keywords": ["test"], "target_agent_id": "agent-1", "priority": 10, "enabled": False},
        ]
        result = router.route("Run the test suite", rules, [])
        assert result is None

    def test_case_insensitive(self):
        router = TaskRouter(routing_mode="keyword")
        rules = [
            {"id": "r1", "keywords": ["Deploy"], "target_agent_id": "agent-ops", "priority": 10, "enabled": True},
        ]
        result = router.route("deploy to production", rules, [])
        assert result is not None


class TestSkillRouting:
    def test_skill_match(self):
        router = TaskRouter(routing_mode="skill_match")
        agents = [
            AgentProfile(agent_id="python-dev", skills=("python", "api")),
            AgentProfile(agent_id="js-dev", skills=("javascript", "react")),
        ]
        result = router.route("Build a python API endpoint", [], agents)
        assert result is not None
        assert result.agent_id == "python-dev"

    def test_no_skill_match(self):
        router = TaskRouter(routing_mode="skill_match")
        agents = [
            AgentProfile(agent_id="a1", skills=("rust",)),
        ]
        result = router.route("Write a blog post about cooking", [], agents)
        assert result is None


class TestRoundRobin:
    def test_distributes_evenly(self):
        router = TaskRouter(routing_mode="round_robin")
        agents = [
            AgentProfile(agent_id="a1"),
            AgentProfile(agent_id="a2"),
            AgentProfile(agent_id="a3"),
        ]
        ids = []
        for i in range(6):
            result = router.route(f"task-{i}", [], agents)
            ids.append(result.agent_id)
        assert ids == ["a1", "a2", "a3", "a1", "a2", "a3"]

    def test_empty_agents(self):
        router = TaskRouter(routing_mode="round_robin")
        result = router.route("task", [], [])
        assert result is None


# ============================================================
# Quality Gates Tests
# ============================================================

class TestQualityGate:
    def test_gate_passes(self):
        gate = QualityGate("qa_score", threshold=8.5)
        result = gate.evaluate({"score": 9.0})
        assert result.passed is True
        assert result.score == 9.0

    def test_gate_fails(self):
        gate = QualityGate("qa_score", threshold=8.5)
        result = gate.evaluate({"score": 7.0})
        assert result.passed is False
        assert "7.0" in result.reason

    def test_exact_threshold_passes(self):
        gate = QualityGate("qa_score", threshold=8.5)
        result = gate.evaluate({"score": 8.5})
        assert result.passed is True


class TestQualityPipeline:
    def test_empty_pipeline_passes(self):
        pipeline = QualityPipeline()
        result = pipeline.evaluate({"score": 5.0})
        assert result.passed is True

    def test_all_gates_pass(self):
        pipeline = QualityPipeline([
            QualityGate("review", 7.0, gate_order=0),
            QualityGate("qa", 8.0, gate_order=1),
        ])
        result = pipeline.evaluate({"score": 9.0})
        assert result.passed is True
        assert len(result.gate_results) == 2

    def test_stops_on_first_failure(self):
        pipeline = QualityPipeline([
            QualityGate("review", 8.0, gate_order=0),
            QualityGate("qa", 9.0, gate_order=1),
        ])
        result = pipeline.evaluate({"score": 7.0})
        assert result.passed is False
        assert result.failed_gate == "review"
        assert len(result.gate_results) == 1  # Stopped at first gate

    def test_second_gate_fails(self):
        pipeline = QualityPipeline([
            QualityGate("review", 7.0, gate_order=0),
            QualityGate("qa", 9.0, gate_order=1),
        ])
        result = pipeline.evaluate({"score": 8.0})
        assert result.passed is False
        assert result.failed_gate == "qa"
        assert len(result.gate_results) == 2

    def test_add_gate(self):
        pipeline = QualityPipeline()
        pipeline.add_gate(QualityGate("review", 7.0, gate_order=1))
        pipeline.add_gate(QualityGate("basic", 5.0, gate_order=0))
        assert len(pipeline.gates) == 2
        assert pipeline.gates[0].gate_type == "basic"  # Sorted by order

    def test_from_db_records(self):
        records = [
            {"gate_type": "review", "threshold": 8.0, "gate_order": 0, "enabled": True},
            {"gate_type": "disabled", "threshold": 5.0, "gate_order": 1, "enabled": False},
            {"gate_type": "qa", "threshold": 9.0, "gate_order": 2, "enabled": True},
        ]
        pipeline = QualityPipeline.from_db_records(records)
        assert len(pipeline.gates) == 2  # Disabled one skipped


# ============================================================
# Orchestrator Tests
# ============================================================

@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(Path(tmp) / "test.db")


@pytest.fixture
def orch(db):
    return TeamOrchestrator(db, "team-1")


class TestOrchestrator:
    def test_submit_task(self, orch):
        task = orch.submit_task("Fix the bug", description="Login fails")
        assert task.title == "Fix the bug"
        assert task.status == "pending"

    def test_submit_empty_title_fails(self, orch):
        with pytest.raises(OrchestratorError, match="title"):
            orch.submit_task("")

    def test_assign_task(self, orch):
        task = orch.submit_task("Task 1")
        assigned = orch.assign_task(task.id, "agent-1")
        assert assigned.status == "assigned"
        assert assigned.assigned_to == "agent-1"

    def test_assign_nonexistent_fails(self, orch):
        with pytest.raises(OrchestratorError, match="not found"):
            orch.assign_task("nonexistent", "agent-1")

    def test_route_and_assign(self, orch):
        task = orch.submit_task("Fix the debug issue")
        router = TaskRouter(routing_mode="keyword")
        rules = [
            {"id": "r1", "keywords": ["fix", "debug"], "target_agent_id": "dev-agent", "priority": 10, "enabled": True},
        ]
        assignment = orch.route_and_assign(task.id, router, rules, [])
        assert assignment is not None
        assert assignment.agent_id == "dev-agent"
        fetched = orch.get_task(task.id)
        assert fetched.status == "assigned"

    def test_submit_result_no_pipeline(self, orch):
        task = orch.submit_task("Task")
        orch.assign_task(task.id, "agent-1")
        updated, pr = orch.submit_result(task.id, {"output": "done"})
        assert updated.status == "completed"
        assert pr is None

    def test_submit_result_passes_pipeline(self, orch):
        task = orch.submit_task("Task")
        orch.assign_task(task.id, "agent-1")
        pipeline = QualityPipeline([QualityGate("qa", 8.0)])
        updated, pr = orch.submit_result(task.id, {"score": 9.0}, pipeline)
        assert updated.status == "completed"
        assert pr.passed is True

    def test_submit_result_fails_pipeline(self, orch):
        task = orch.submit_task("Task")
        orch.assign_task(task.id, "agent-1")
        pipeline = QualityPipeline([QualityGate("qa", 9.0)])
        updated, pr = orch.submit_result(task.id, {"score": 7.0}, pipeline)
        assert updated.status == "review"
        assert pr.passed is False

    def test_retry_task(self, orch):
        task = orch.submit_task("Task")
        orch.assign_task(task.id, "agent-1")
        retried = orch.retry_task(task.id)
        assert retried is not None
        assert retried.status == "assigned"

    def test_three_strikes_fails(self, orch):
        task = orch.submit_task("Task")
        orch.assign_task(task.id, "agent-1")
        orch.retry_task(task.id, max_retries=3)  # 1
        orch.retry_task(task.id, max_retries=3)  # 2
        orch.retry_task(task.id, max_retries=3)  # 3
        result = orch.retry_task(task.id, max_retries=3)  # Over limit
        assert result is None
        fetched = orch.get_task(task.id)
        assert fetched.status == "failed"

    def test_list_tasks(self, orch):
        orch.submit_task("Task 1")
        orch.submit_task("Task 2")
        tasks = orch.list_tasks()
        assert len(tasks) == 2

    def test_list_tasks_by_status(self, orch):
        t1 = orch.submit_task("Task 1")
        orch.submit_task("Task 2")
        orch.assign_task(t1.id, "agent-1")
        assigned = orch.list_tasks(status="assigned")
        assert len(assigned) == 1
        pending = orch.list_tasks(status="pending")
        assert len(pending) == 1

    def test_get_stats(self, orch):
        t1 = orch.submit_task("Task 1")
        orch.submit_task("Task 2")
        orch.assign_task(t1.id, "agent-1")
        stats = orch.get_stats()
        assert stats["total"] == 2
        assert stats["by_status"]["pending"] == 1
        assert stats["by_status"]["assigned"] == 1


# ============================================================
# Templates Tests
# ============================================================

class TestTemplates:
    def test_list_team_templates(self):
        templates = list_team_templates()
        assert len(templates) == 3
        names = [t["id"] for t in templates]
        assert "solo" in names
        assert "small_team" in names
        assert "enterprise" in names

    def test_get_team_template(self):
        tmpl = get_team_template("solo")
        assert tmpl is not None
        assert tmpl["name"] == "Solo Agent"
        assert len(tmpl["agents"]) == 1

    def test_get_nonexistent_template(self):
        assert get_team_template("nonexistent") is None

    def test_list_service_templates(self):
        templates = list_service_templates()
        assert len(templates) == 3
        names = [t["id"] for t in templates]
        assert "ai_api" in names

    def test_get_service_template(self):
        tmpl = get_service_template("ai_api")
        assert tmpl is not None
        assert tmpl["category"] == "ai"

    def test_small_team_has_routing(self):
        tmpl = get_team_template("small_team")
        assert "routing_rules" in tmpl
        assert len(tmpl["routing_rules"]) == 3

    def test_enterprise_has_quality_gates(self):
        tmpl = get_team_template("enterprise")
        assert len(tmpl["quality_gates"]) == 3
