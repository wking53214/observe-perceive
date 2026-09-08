"""
FDA 510(k) Pre-Submission Readiness Checklist
==============================================

This checklist maps the unified OBSERVE + PERCEIVE system to FDA expectations for
pediatric clinical decision-support software (510(k) substantial equivalence pathway).

Use this before FDA meetings or regulatory filings. Each item maps to:
  - Regulatory requirement (from FDA guidance)
  - System implementation
  - Evidence location (in this codebase)
  - Sign-off responsibility

STATUS CODES:
  ✅ = Ready: the evidence cited exists in this repository AND the item names
       at least one test, under "tests", that exists and runs in CI. A ✅ with
       no runnable test is not allowed; test_fda_510k_checklist.py enforces it.
  ⏳ = In progress (code exists, needs validation data or a formal artifact)
  ❌ = Not yet done (architectural change needed)
  🔄 = Deferred (Phase 2+)

AUDIT NOTE (2026-09-07): an external audit found eleven items marked ✅ with
no test behind any of them, two citing README_CONSOLIDATED.md, which does
not exist in this repository, and one citing "241 tests verify" where the
verifying tests were never named. Every ✅ below now names its tests, and
five items were downgraded to ⏳ because the evidence did not support
"ready". This file is exported by compliance_exporters.py, so what it says
is what a reviewer sees.

---
"""

from typing import Dict, Any

# FDA 510(k) CHECKLIST FOR CLINICAL DECISION-SUPPORT SOFTWARE

CHECKLIST = {
    "I. DEVICE CLASSIFICATION & INTENDED USE": {
        "I.1": {
            "requirement": "Intended use statement (pediatric early-warning, NOT treatment recommendation)",
            "status": "⏳",
            "evidence": "README.md (pediatric sepsis monitoring named as the representative application), clinical_governance_system.py docstring",
            "sign_off": "Product owner",
            "notes": "System provides risk assessment + escalation routing. Physician retains all clinical decisions. "
                     "Downgraded 2026-09-07: the README describes an architecture with a representative application; "
                     "no formal intended-use statement in regulatory form exists yet.",
        },
        "I.2": {
            "requirement": "Predicate device identification (or 505(b)(2) equivalence argument)",
            "status": "⏳",
            "evidence": "Comparable to: Philips IntelliVue platform, GE Carescape, Hospira MedNet",
            "sign_off": "Regulatory affairs",
            "notes": "System is decision-support for triage, not primary monitoring. Predicate: FDA-cleared pediatric early-warning systems.",
        },
        "I.3": {
            "requirement": "Risk classification (Class II expected for decision-support, not Class III treatment)",
            "status": "⏳",
            "evidence": "Fail-open governance, advisory-default policies, physician-in-loop escalation",
            "sign_off": "Regulatory affairs + Risk management team",
            "notes": "Not autonomous treatment. Escalations are advisory alerts to clinical staff.",
        },
    },
    "II. PERFORMANCE & FUNCTIONALITY": {
        "II.1": {
            "requirement": "Algorithm validation (sensitivity, specificity, NPV, PPV on clinical datasets)",
            "status": "⏳",
            "evidence": "deterioration_simulator.py (2/5 pass on synthetic); AccuracyMonitor (requires real labels)",
            "sign_off": "Clinical team + biostatistician",
            "notes": "Synthetic validation shows septic spiral detected on-time. Phase 1.5 pilot will collect real outcomes for NPV/PPV.",
        },
        "II.2": {
            "requirement": "Sensitivity analysis (impact of missing inputs, sensor faults)",
            "status": "⏳",
            "evidence": "adversarial_sensor_faults.py (6/11 fault cases safe); validate_vitals() handles NaN/Inf. "
                        "test_adversarial_sensor_faults.py converts the 5 unsafe cases into pytest skips, so the suite "
                        "cannot fail on them -- downgraded 2026-09-07 until those cases pass or are asserted.",
            "sign_off": "QA + Clinical team",
            "notes": "NaN/Inf → WARNING escalation (safe default). Stuck sensors flagged as gaps (Phase 2: Kalman + adversarial engine).",
        },
        "II.3": {
            "requirement": "Failure modes & mitigation (what if network down, sensor fail, governance crash?)",
            "status": "✅",
            "evidence": "OBSERVE: data-integrity faults (validate_vitals, sanitize_context); PERCEIVE: governance failure fails open for escalation (clinical_governance_system.py); RED_TEAM_REPORT.md",
            "tests": ["test_clinical_governance_system.py::test_governance_exception_fails_open_for_escalation", "test_context_sanitization.py"],
            "sign_off": "Risk management + FMEA lead",
            "notes": "Single-point failures: PERCEIVE crash (fail-open to approval). Multi-point: network + sensor + governance (documented risk).",
        },
        "II.4": {
            "requirement": "Determinism & reproducibility (FDA expects bit-for-bit reproducibility on identical inputs)",
            "status": "✅",
            "evidence": "decision_fingerprint reproducible; audit_hash differs (timestamped, by design)",
            "tests": ["test_integration_consolidated.py::test_first_call_is_deterministic", "test_compliance_exporters.py::test_determinism_attestation_present", "test_governance_orchestrator_basic.py::test_state_commitment_is_deterministic"],
            "sign_off": "QA + Engineering",
            "notes": "Decisions are deterministic. Audit chains include timestamps (tamper-evidence intent). Documented in CHANGELOG.md.",
        },
        "II.5": {
            "requirement": "Edge cases (extreme values, missing data, ambiguous decisions)",
            "status": "✅",
            "evidence": "VITALS_PHYSICAL_BOUNDS; abstention logic; BayesianFusion excludes abstainers; hard-rule bypass on syndrome",
            "tests": ["test_context_sanitization.py", "test_observe_consolidated.py::test_all_abstained_degenerate_fallback"],
            "sign_off": "QA",
            "notes": "Out-of-range vitals handled. Missing data → abstain or low confidence. Fusion weights only present engines.",
        },
    },
    "III. SOFTWARE SAFETY & SECURITY": {
        "III.1": {
            "requirement": "IEC 62304 compliance (software development lifecycle documentation)",
            "status": "⏳",
            "evidence": "Version control (git); unit tests (241 passing); requirements traceability (CHANGELOG.md maps findings to fixes)",
            "sign_off": "QA lead + Software architect",
            "notes": "Need formal traceability matrix (findings → test cases → code). Current: informal.",
        },
        "III.2": {
            "requirement": "Cybersecurity (tamper-detection, access control, encryption in transit)",
            "status": "⏳",
            "evidence": "SHA256-chained audit (tamper-evident, tested); HIPAA exporters require a runtime salt; no hardcoded secrets (pattern sweep 2026-09-07: 0 hits). "
                        "Access control and encryption in transit are not implemented in this codebase -- downgraded 2026-09-07.",
            "tests": ["test_observe_consolidated.py::test_tamper_detection", "test_perceive_consolidated.py::test_chain_integrity_valid", "test_compliance_exporters.py::test_missing_salt_raises"],
            "sign_off": "Security architect",
            "notes": "Single-node (NCH): no network auth needed. Multi-node (Phase 2): DGK needs key management.",
        },
        "III.3": {
            "requirement": "De-identification (HIPAA safe harbor or expert determination)",
            "status": "✅",
            "evidence": "pseudonymize_patient_id with runtime salt; HIPAA/GDPR exporters; no hardcoded identifiers",
            "tests": ["test_compliance_exporters.py::test_pseudonym_does_not_contain_raw_id", "test_compliance_exporters.py::test_pseudonym_salt_changes_output", "test_compliance_exporters.py::test_missing_salt_raises"],
            "sign_off": "Privacy officer + Compliance",
            "notes": "Salt is required runtime arg. No default keys. GDPR exporter includes data-subject-rights paths.",
        },
        "III.4": {
            "requirement": "Audit trail (immutable logs of all decisions and policy changes)",
            "status": "✅",
            "evidence": "ImmutableAuditLedger (OBSERVE); audit entries in PERCEIVE; SHA256-chained; verify_integrity() works",
            "tests": ["test_observe_consolidated.py::test_chain_integrity_valid_after_appends", "test_observe_consolidated.py::test_tamper_detection", "test_perceive_consolidated.py::test_chain_integrity_valid", "test_governance_orchestrator.py::test_audit_chain_linking"],
            "sign_off": "Compliance + IT security",
            "notes": "In-memory only (Phase 1). Persistence needed for production (PostgreSQL + replication recommended).",
        },
    },
    "IV. VALIDATION & TESTING": {
        "IV.1": {
            "requirement": "Unit test coverage (high-risk algorithms)",
            "status": "✅",
            "evidence": "Unit suites for OBSERVE engines, PERCEIVE gates, fusion and audit; adapter suites per seam. Count: python -m pytest --co -q",
            "tests": ["test_observe_consolidated.py", "test_perceive_consolidated.py", "test_kalman_trajectory.py", "test_reserve_control.py", "test_capacity_planning.py"],
            "sign_off": "QA",
            "notes": "Coverage gaps: physiological_reserve axis-by-axis (added post-hoc). Kalman test coverage adequate.",
        },
        "IV.2": {
            "requirement": "Integration testing (end-to-end OBSERVE → PERCEIVE → audit)",
            "status": "✅",
            "evidence": "test_integration_consolidated.py (6 tests) and test_governance_orchestrator.py (end-to-end chain, needs the sibling checkouts CI provides); determinism verified; governance fail-open tested",
            "tests": ["test_integration_consolidated.py", "test_governance_orchestrator.py::test_complete_approval_flow", "test_clinical_governance_system.py::test_governance_exception_fails_open_for_escalation"],
            "sign_off": "QA",
            "notes": "Tests cover single-node path fully. Multi-node DGK needs separate integration tests (Phase 2).",
        },
        "IV.3": {
            "requirement": "Clinical validation (algorithm performance on real pediatric cases)",
            "status": "⏳",
            "evidence": "deterioration_simulator.py (5 scenarios); adversarial_sensor_faults.py (11 fault cases); synthetic only",
            "sign_off": "Clinical team + Pediatrician",
            "notes": "Synthetic shows gaps (viral fever, hypoxia, reactive airway need tuning). Phase 1.5 pilot will validate on real cases.",
        },
        "IV.4": {
            "requirement": "Usability testing (clinicians can understand escalation reasons)",
            "status": "❌",
            "evidence": "triggered_rules human-readable; engine names clear; lacking actual clinician interface testing",
            "sign_off": "UX + Clinical team",
            "notes": "Output is code-facing (JSON). UI layer needed for clinician use. Out of scope for this phase.",
        },
    },
    "V. DOCUMENTATION FOR SUBMISSION": {
        "V.1": {
            "requirement": "Device description (what is it, how does it work, predicate equivalence)",
            "status": "⏳",
            "evidence": "README.md, GOVERNANCE_ORCHESTRATION.md, architecture diagram (needed), clinical_governance_system.py",
            "sign_off": "Regulatory affairs",
            "notes": "README covers architecture. Need formal 510(k) device description document (~5 pages).",
        },
        "V.2": {
            "requirement": "Substantial equivalence argument (how it's similar to predicate + differences managed)",
            "status": "⏳",
            "evidence": "Predicate: multi-parameter pediatric monitors (Philips, GE, etc.). Differences: AI-based fusion, multi-node consensus",
            "sign_off": "Regulatory affairs",
            "notes": "Claim: 'Software enhancement to existing monitoring platforms, adding algorithmic risk assessment.' Needs written SE comparison.",
        },
        "V.3": {
            "requirement": "Non-clinical and clinical performance data",
            "status": "⏳",
            "evidence": "Computational performance: <500ms/decision (not optimized). Clinical: synthetic 2/5 scenarios, real data pending",
            "sign_off": "Engineering + Clinical",
            "notes": "Performance is adequate. Clinical data collection plan needed (Phase 1.5 pilot protocol).",
        },
        "V.4": {
            "requirement": "Risk analysis & mitigation (FMEA or equivalent)",
            "status": "⏳",
            "evidence": "RED_TEAM_REPORT.md covers known issues; hazard analysis in README. Needs formal FMEA table.",
            "sign_off": "Risk management",
            "notes": "Hazards identified: sensor faults (mitigated), governance crash (fail-open), network (mitigated in single-node).",
        },
        "V.5": {
            "requirement": "Labeling (user manual, installation guide, warnings)",
            "status": "❌",
            "evidence": "Code comments adequate; user guide not yet written",
            "sign_off": "Regulatory affairs + Clinical documentation",
            "notes": "Needed: installation guide, escalation interpretation, threshold tuning, troubleshooting.",
        },
        "V.6": {
            "requirement": "Summary of FDA interactions (if any pre-submission meetings held)",
            "status": "⏳",
            "evidence": "No FDA pre-submission yet; timeline TBD",
            "sign_off": "Regulatory affairs",
            "notes": "Recommend Type B meeting (informal, guidance-seeking) before formal 510(k).",
        },
    },
    "VI. MANUFACTURING & POST-MARKET": {
        "VI.1": {
            "requirement": "Software version control & configuration management",
            "status": "⏳",
            "evidence": "Git repo with CI on every push; setup.py version 1.0.0; CHANGELOG.md. No release tags exist and pytest.ini does not pin test files -- downgraded 2026-09-07.",
            "sign_off": "IT",
            "notes": "Current: local development only. Need CI/CD pipeline + release tagging (Phase 2).",
        },
        "VI.2": {
            "requirement": "Post-market surveillance plan (how to detect if algorithm drifts)",
            "status": "⏳",
            "evidence": "AccuracyMonitor.report() includes drift detection; notes field for review",
            "sign_off": "Clinical team + Compliance",
            "notes": "Plan: weekly accuracy reports, monthly review with pediatrician, alert on drift (sensitivity -10% threshold).",
        },
        "VI.3": {
            "requirement": "Cybersecurity update & patch management",
            "status": "⏳",
            "evidence": "Stdlib-only (no third-party deps). Need process for security updates to Python stdlib.",
            "sign_off": "IT security",
            "notes": "Phase 2: formalize patch management (e.g., when Python 3.8 EOL, migrate to 3.11).",
        },
        "VI.4": {
            "requirement": "Complaint handling & adverse event reporting",
            "status": "❌",
            "evidence": "No complaint process yet",
            "sign_off": "Compliance + Quality assurance",
            "notes": "Needed: complaint form, triage process, MAUDE reporting workflow (Phase 2).",
        },
    },
}


# ============================================================================
# SCORING & RECOMMENDATIONS
# ============================================================================

def score_readiness() -> Dict[str, Any]:
    """Compute readiness score by category."""
    categories = {}
    for cat_name, items in CHECKLIST.items():
        statuses = [item["status"] for item in items.values()]
        ready = statuses.count("✅")
        in_progress = statuses.count("⏳")
        not_done = statuses.count("❌")
        deferred = statuses.count("🔄")
        
        score_pct = 100 * ready / len(statuses) if statuses else 0
        categories[cat_name] = {
            "ready": ready,
            "in_progress": in_progress,
            "not_done": not_done,
            "deferred": deferred,
            "score": score_pct,
        }
    return categories


def report() -> str:
    """Generate FDA readiness report."""
    scores = score_readiness()
    
    lines = [
        "=" * 100,
        "FDA 510(k) READINESS ASSESSMENT — OBSERVE + PERCEIVE",
        "=" * 100,
        "",
    ]
    
    total_items = sum(len(items) for items in CHECKLIST.values())
    total_ready = sum(s["ready"] for s in scores.values())
    total_in_progress = sum(s["in_progress"] for s in scores.values())
    total_not_done = sum(s["not_done"] for s in scores.values())
    
    lines.extend([
        f"OVERALL: {total_ready}/{total_items} items ready ({100*total_ready//total_items}%)",
        f"  ✅ Ready: {total_ready}",
        f"  ⏳ In Progress: {total_in_progress}",
        f"  ❌ Not Done: {total_not_done}",
        "",
    ])
    
    for cat_name, s in scores.items():
        pct = int(s["score"])
        icon = "🟢" if pct >= 80 else "🟡" if pct >= 50 else "🔴"
        lines.append(f"{icon} {cat_name}: {pct}% ({s['ready']}/{s['ready']+s['in_progress']+s['not_done']} ready)")
    
    lines.extend([
        "",
        "=" * 100,
        "DETAILED CHECKLIST BY CATEGORY:",
        "=" * 100,
        "",
    ])
    
    for cat_name, items in CHECKLIST.items():
        lines.append(f"\n{cat_name}")
        lines.append("-" * 100)
        for item_id, item in items.items():
            icon = item["status"]
            lines.append(f"  {icon} {item_id}: {item['requirement']}")
            lines.append(f"     Evidence: {item['evidence']}")
            if item["notes"]:
                lines.append(f"     Note: {item['notes']}")
            lines.append("")
    
    lines.extend([
        "=" * 100,
        "CRITICAL PATH TO FDA SUBMISSION:",
        "=" * 100,
        "",
        "Phase 1 (Now — pre-pilot):",
        "  1. Pediatrician review + sign-off on behavioral thresholds (1–2 days)",
        "  2. Finalize device description for 510(k) (1 week)",
        "  3. Document substantial equivalence argument vs predicate (1 week)",
        "  4. Plan Phase 1.5 pilot protocol (1–2 weeks)",
        "",
        "Phase 1.5 (Pilot, 90 days):",
        "  1. Deploy in read-only mode at NCH",
        "  2. Collect real clinical outcomes; label deterioration cases",
        "  3. Validate algorithm performance (sensitivity, specificity, NPV, PPV)",
        "  4. Detect any accuracy drift vs synthetic validation",
        "",
        "Phase 2 (Post-pilot, 4–6 weeks):",
        "  1. Type B FDA pre-submission meeting (informal guidance)",
        "  2. Finalize FMEA and risk controls",
        "  3. Formalize post-market surveillance plan",
        "  4. Prepare complete 510(k) submission package",
        "",
        "Phase 2+ (Regulatory, 6–12 months):",
        "  1. FDA review (typically 30–90 days for Class II devices)",
        "  2. Respond to questions / amendments",
        "  3. Receive clearance letter",
        "  4. Commercial distribution",
        "",
        "=" * 100,
    ])
    
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
